"""
Windows 屏幕亮度 / 色温调节脚本（纯标准库，无需 pip 安装任何东西）

功能:
  * 硬件亮度: 笔记本内屏走 WMI, 外接显示器走 DDC/CI (需显示器菜单里开启 DDC/CI)
  * 色温:     通过 Gamma 曲线 (SetDeviceGammaRamp) 调整, 1900K ~ 6500K
  * 软件调暗: 通过 Gamma 曲线整体压暗, 适合不支持 DDC/CI 的显示器
  * 定时:     按时间表自动切换色温 / 亮度, 支持渐变过渡和手动选择时区

用法:
  python screen_adjust.py --gui                 打开滑块窗口 (含定时设置)
  python screen_adjust.py --info                查看显示器和当前亮度
  python screen_adjust.py -b 60                 硬件亮度设为 60%
  python screen_adjust.py -t 4500               色温设为 4500K
  python screen_adjust.py -t 4000 -d 80         色温 4000K + 软件调暗到 80%
  python screen_adjust.py --reset               恢复默认色温 / Gamma
  python screen_adjust.py --unlock              (管理员) 解除 Windows 对 Gamma 范围的限制

定时:
  python screen_adjust.py --show-schedule       查看时间表和此刻的目标值
  python screen_adjust.py --schedule            在后台按时间表持续运行 (Ctrl+C 退出)
  python screen_adjust.py --schedule --tz +8    临时指定时区 (覆盖配置文件)
  python screen_adjust.py --autostart on        开机 (登录) 自动以无窗口方式运行定时
  python screen_adjust.py --autostart off       取消开机自启

  时间表保存在脚本同目录的 screen_schedule.json, 可在 GUI 里编辑, 也可直接改文件。
  时区写法: "local" (跟随系统) / "+8" / "UTC+8" / "UTC-05:30" / "Asia/Shanghai"
  (IANA 名称如 Asia/Shanghai 在 Windows 上需要 pip install tzdata, 固定偏移则不需要)
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import math
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform != "win32":
    sys.exit("此脚本仅支持 Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


# --------------------------------------------------------------------------- #
# Win32 结构体与函数签名
# --------------------------------------------------------------------------- #
class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
        ("szDevice", wt.WCHAR * 32),
    ]


class PHYSICAL_MONITOR(ctypes.Structure):
    _fields_ = [
        ("hPhysicalMonitor", wt.HANDLE),
        ("szPhysicalMonitorDescription", wt.WCHAR * 128),
    ]


MonitorEnumProc = ctypes.WINFUNCTYPE(
    wt.BOOL, wt.HANDLE, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM
)

user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.c_void_p, MonitorEnumProc, wt.LPARAM]
user32.EnumDisplayMonitors.restype = wt.BOOL
user32.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
user32.GetMonitorInfoW.restype = wt.BOOL

gdi32.CreateDCW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_void_p]
gdi32.CreateDCW.restype = wt.HDC
gdi32.DeleteDC.argtypes = [wt.HDC]
gdi32.SetDeviceGammaRamp.argtypes = [wt.HDC, ctypes.c_void_p]
gdi32.SetDeviceGammaRamp.restype = wt.BOOL

dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wt.BOOL
dxva2.GetPhysicalMonitorsFromHMONITOR.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
dxva2.GetPhysicalMonitorsFromHMONITOR.restype = wt.BOOL
dxva2.DestroyPhysicalMonitors.argtypes = [wt.DWORD, ctypes.POINTER(PHYSICAL_MONITOR)]
dxva2.GetVCPFeatureAndVCPFeatureReply.argtypes = [
    wt.HANDLE, wt.BYTE, ctypes.c_void_p, ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD)
]
dxva2.GetVCPFeatureAndVCPFeatureReply.restype = wt.BOOL
dxva2.SetVCPFeature.argtypes = [wt.HANDLE, wt.BYTE, wt.DWORD]
dxva2.SetVCPFeature.restype = wt.BOOL

VCP_BRIGHTNESS = 0x10
CREATE_NO_WINDOW = 0x08000000


# --------------------------------------------------------------------------- #
# 显示器枚举
# --------------------------------------------------------------------------- #
def enum_monitors():
    """返回 [(hmonitor, 设备名如 \\\\.\\DISPLAY1), ...]"""
    handles = []

    def _cb(hmon, _hdc, _rect, _lp):
        handles.append(hmon)
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)
    result = []
    for h in handles:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(h, ctypes.byref(info)):
            result.append((h, info.szDevice))
    return result


# --------------------------------------------------------------------------- #
# 硬件亮度: DDC/CI (外接显示器)
# --------------------------------------------------------------------------- #
def _physical_monitors(hmon):
    n = wt.DWORD()
    if not dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, ctypes.byref(n)) or n.value == 0:
        return None
    arr = (PHYSICAL_MONITOR * n.value)()
    if not dxva2.GetPhysicalMonitorsFromHMONITOR(hmon, n.value, arr):
        return None
    return arr


def ddc_get_brightness():
    """返回 [(描述, 当前值, 最大值), ...]"""
    out = []
    for hmon, _ in enum_monitors():
        arr = _physical_monitors(hmon)
        if arr is None:
            continue
        try:
            for pm in arr:
                cur, mx = wt.DWORD(), wt.DWORD()
                if dxva2.GetVCPFeatureAndVCPFeatureReply(
                    pm.hPhysicalMonitor, VCP_BRIGHTNESS, None, ctypes.byref(cur), ctypes.byref(mx)
                ):
                    out.append((pm.szPhysicalMonitorDescription, cur.value, mx.value))
        finally:
            dxva2.DestroyPhysicalMonitors(len(arr), arr)
    return out


def ddc_set_brightness(percent):
    """对所有支持 DDC/CI 的显示器设置亮度, 返回成功的数量"""
    ok = 0
    for hmon, _ in enum_monitors():
        arr = _physical_monitors(hmon)
        if arr is None:
            continue
        try:
            for pm in arr:
                cur, mx = wt.DWORD(), wt.DWORD()
                if not dxva2.GetVCPFeatureAndVCPFeatureReply(
                    pm.hPhysicalMonitor, VCP_BRIGHTNESS, None, ctypes.byref(cur), ctypes.byref(mx)
                ):
                    continue
                value = round(percent / 100 * (mx.value or 100))
                if dxva2.SetVCPFeature(pm.hPhysicalMonitor, VCP_BRIGHTNESS, value):
                    ok += 1
        finally:
            dxva2.DestroyPhysicalMonitors(len(arr), arr)
    return ok


# --------------------------------------------------------------------------- #
# 硬件亮度: WMI (笔记本内置屏)
# --------------------------------------------------------------------------- #
def _powershell(cmd):
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    return r.returncode, r.stdout.strip()


def wmi_get_brightness():
    code, out = _powershell(
        "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
        "-ErrorAction Stop).CurrentBrightness"
    )
    if code == 0 and out:
        try:
            return int(out.splitlines()[0])
        except ValueError:
            pass
    return None


def wmi_set_brightness(percent):
    code, _ = _powershell(
        "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods "
        "-ErrorAction Stop | Invoke-CimMethod -MethodName WmiSetBrightness "
        f"-Arguments @{{Timeout=0; Brightness={int(percent)}}} -ErrorAction Stop | Out-Null"
    )
    return code == 0


def set_hw_brightness(percent):
    percent = max(0, min(100, int(percent)))
    wmi_ok = wmi_set_brightness(percent)
    ddc_ok = ddc_set_brightness(percent)
    return wmi_ok, ddc_ok


# --------------------------------------------------------------------------- #
# 色温 / 软件调暗: Gamma Ramp
# --------------------------------------------------------------------------- #
def _kelvin_to_rgb_raw(kelvin):
    """Tanner Helland 近似算法, 返回 0~255 的 (r, g, b)"""
    t = kelvin / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    clamp = lambda v: max(0.0, min(255.0, v))
    return clamp(r), clamp(g), clamp(b)


def kelvin_to_rgb(kelvin):
    """返回 0~1 的通道系数, 并以 6500K 归一化为 (1,1,1)"""
    ref = _kelvin_to_rgb_raw(6500)
    cur = _kelvin_to_rgb_raw(kelvin)
    return tuple(min(1.0, c / r) for c, r in zip(cur, ref))


def set_gamma(kelvin=6500, dim=100):
    """kelvin: 1900~6500, dim: 软件亮度 10~100 (%)。返回 (成功数, 总数)"""
    kelvin = max(1900, min(6500, int(kelvin)))
    dim = max(10, min(100, int(dim))) / 100.0
    rf, gf, bf = kelvin_to_rgb(kelvin)

    ramp = (wt.WORD * 256 * 3)()
    for i in range(256):
        base = i / 255.0 * 65535 * dim
        ramp[0][i] = int(min(65535, base * rf))
        ramp[1][i] = int(min(65535, base * gf))
        ramp[2][i] = int(min(65535, base * bf))

    ok = total = 0
    for _, device in enum_monitors():
        total += 1
        hdc = gdi32.CreateDCW(device, None, None, None)
        if not hdc:
            continue
        try:
            if gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)):
                ok += 1
        finally:
            gdi32.DeleteDC(hdc)
    return ok, total


def unlock_gamma_range():
    """写注册表解除 Gamma 范围限制 (需管理员权限, 重启或注销后生效)"""
    import winreg
    key = winreg.CreateKeyEx(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ICM",
        0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY,
    )
    with key:
        winreg.SetValueEx(key, "GdiIcmGammaRange", 0, winreg.REG_DWORD, 256)


# --------------------------------------------------------------------------- #
# 时区
# --------------------------------------------------------------------------- #
_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.I)


def parse_tz(text):
    """'local' -> None (系统时区); '+8' / 'UTC+05:30' -> 固定偏移; 'Asia/Shanghai' -> zoneinfo"""
    s = (text or "").strip()
    if not s or s.lower() in ("local", "本机"):
        return None
    if s.upper() in ("UTC", "GMT", "Z"):
        return timezone.utc
    m = _OFFSET_RE.match(s)
    if m:
        sign, hh, mm = m.group(1), int(m.group(2)), int(m.group(3) or 0)
        if hh > 14 or mm >= 60:
            raise ValueError(f"无效的时区偏移: {s}")
        delta = timedelta(hours=hh, minutes=mm)
        return timezone(-delta if sign == "-" else delta)
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(s)
    except Exception:
        raise ValueError(f"无法识别时区 '{s}' (IANA 名称需要先 pip install tzdata, "
                         f"或改用 +8 / UTC-05:30 这种写法)")


def tz_choices():
    offsets = list(range(-12 * 60, 14 * 60 + 1, 60))
    offsets += [-9 * 60 - 30, -3 * 60 - 30, 3 * 60 + 30, 4 * 60 + 30, 5 * 60 + 30,
                5 * 60 + 45, 6 * 60 + 30, 8 * 60 + 45, 9 * 60 + 30, 10 * 60 + 30, 12 * 60 + 45]
    items = ["local"]
    for o in sorted(offsets):
        sign = "-" if o < 0 else "+"
        items.append(f"UTC{sign}{abs(o) // 60:02d}:{abs(o) % 60:02d}")
    try:  # 装了 tzdata 时额外提供几个常用的 IANA 时区 (能自动处理夏令时)
        from zoneinfo import ZoneInfo
        ZoneInfo("Asia/Shanghai")
        items += ["Asia/Shanghai", "Asia/Tokyo", "Asia/Singapore", "Europe/London",
                  "Europe/Berlin", "America/New_York", "America/Chicago",
                  "America/Los_Angeles", "Australia/Sydney"]
    except Exception:
        pass
    return items


def now_in_tz(tz):
    return datetime.now(tz) if tz is not None else datetime.now().astimezone()


# --------------------------------------------------------------------------- #
# 定时配置
# --------------------------------------------------------------------------- #
CONFIG_PATH = Path(__file__).resolve().with_name("screen_schedule.json")

DEFAULT_CONFIG = {
    "timezone": "local",
    "fade_minutes": 30,       # 到点后用多少分钟渐变到新值, 0 = 立即切换
    "interval_seconds": 30,   # 后台检查间隔
    "points": [
        {"time": "07:00", "temp": 6500, "dim": 100, "brightness": 80},
        {"time": "19:00", "temp": 4500, "dim": 100, "brightness": 60},
        {"time": "22:30", "temp": 3600, "dim": 100, "brightness": 40},
    ],
}


def parse_hhmm(s):
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", str(s))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"时间格式应为 HH:MM, 收到 '{s}'")
    return int(m.group(1)) * 60 + int(m.group(2))


def normalize_point(p):
    """校验并规范化一个时间点, 出错抛 ValueError"""
    minute = parse_hhmm(p.get("time", ""))
    temp = int(p.get("temp", 6500))
    dim = int(p.get("dim", 100))
    b = p.get("brightness")
    b = None if b in (None, "", "-") else int(b)
    if not 1900 <= temp <= 6500:
        raise ValueError("色温需在 1900~6500 之间")
    if not 10 <= dim <= 100:
        raise ValueError("软件调暗需在 10~100 之间")
    if b is not None and not 0 <= b <= 100:
        raise ValueError("硬件亮度需在 0~100 之间, 留空表示不调")
    out = {"time": f"{minute // 60:02d}:{minute % 60:02d}", "temp": temp, "dim": dim,
           "brightness": b}
    if p.get("fade") is not None:  # 可选: 单个时间点自己的渐变时长
        out["fade"] = int(p["fade"])
    return out


def load_config(path=CONFIG_PATH):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if Path(path).exists():
        with open(path, encoding="utf-8") as f:
            cfg.update(json.load(f))
    cfg["points"] = sorted((normalize_point(p) for p in cfg.get("points", [])),
                           key=lambda p: parse_hhmm(p["time"]))
    parse_tz(cfg["timezone"])  # 提前校验
    return cfg


def save_config(cfg, path=CONFIG_PATH):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def compute_target(cfg, now):
    """根据时间表算出此刻应有的 {temp, dim, brightness}; 时间表为空返回 None"""
    pts = sorted(cfg["points"], key=lambda p: parse_hhmm(p["time"]))
    if not pts:
        return None
    cur_min = now.hour * 60 + now.minute + now.second / 60
    idx = len(pts) - 1  # 早于今天第一个点 -> 沿用昨天最后一个点
    for i, p in enumerate(pts):
        if parse_hhmm(p["time"]) <= cur_min:
            idx = i
    cur, prev = pts[idx], pts[idx - 1]
    elapsed = (cur_min - parse_hhmm(cur["time"])) % 1440
    fade = cur.get("fade", cfg.get("fade_minutes", 0))

    if fade > 0 and elapsed < fade:
        k = elapsed / fade
        lerp = lambda a, b: a + (b - a) * k
        temp, dim = lerp(prev["temp"], cur["temp"]), lerp(prev["dim"], cur["dim"])
        if cur["brightness"] is not None and prev["brightness"] is not None:
            bri = lerp(prev["brightness"], cur["brightness"])
        else:
            bri = cur["brightness"]
    else:
        temp, dim, bri = cur["temp"], cur["dim"], cur["brightness"]
    return {"temp": round(temp), "dim": round(dim),
            "brightness": None if bri is None else round(bri),
            "point": cur["time"], "fading": fade > 0 and elapsed < fade}


class Scheduler:
    """每次 tick 计算目标并应用。Gamma 每次都重设 (锁屏后会被系统重置),
    硬件亮度只在数值变化时才写 (DDC/CI 较慢)。"""

    def __init__(self, hw_async=False):
        self.hw_async = hw_async
        self.last_hw = None
        self._hw_thread = None

    def tick(self, cfg, tz_override=None):
        tz = parse_tz(tz_override if tz_override is not None else cfg["timezone"])
        now = now_in_tz(tz)
        target = compute_target(cfg, now)
        if target is None:
            return now, None, None
        gamma = set_gamma(target["temp"], target["dim"])
        b = target["brightness"]
        if b is not None and b != self.last_hw:
            if not self.hw_async:
                wmi_ok, ddc_ok = set_hw_brightness(b)
                if wmi_ok or ddc_ok:
                    self.last_hw = b
            elif not (self._hw_thread and self._hw_thread.is_alive()):
                self.last_hw = b
                self._hw_thread = threading.Thread(target=set_hw_brightness, args=(b,), daemon=True)
                self._hw_thread.start()
        return now, target, gamma


# --------------------------------------------------------------------------- #
# 单实例锁 / 开机自启
# --------------------------------------------------------------------------- #
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
ERROR_ALREADY_EXISTS = 183
_MUTEX_NAME = "Local\\screen_adjust_scheduler"


def acquire_scheduler_lock():
    """防止两个定时器同时运行互相打架。成功返回句柄, 已有实例返回 None"""
    h = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(h)
        return None
    return h


def release_scheduler_lock(h):
    if h:
        kernel32.CloseHandle(h)


_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "ScreenAdjustSchedule"


def set_autostart(enable, config_path=CONFIG_PATH):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            exe = Path(sys.executable)
            pyw = exe.with_name("pythonw.exe")
            exe = pyw if pyw.exists() else exe
            cmd = (f'"{exe}" "{Path(__file__).resolve()}" --schedule '
                   f'--config "{Path(config_path).resolve()}"')
            winreg.SetValueEx(key, _RUN_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, _RUN_NAME)
            except FileNotFoundError:
                pass


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_NAME)
            return True
    except OSError:
        return False


def run_schedule_loop(cfg_path, tz_override=None):
    lock = acquire_scheduler_lock()
    if lock is None:
        sys.exit("已有一个定时实例在运行 (可能是开机自启或 GUI 里开着定时)")
    sched = Scheduler()
    last_key = None
    print(f"定时已启动, 配置: {cfg_path}  (Ctrl+C 退出)")
    try:
        while True:
            try:
                cfg = load_config(cfg_path)  # 每轮重新读取, 改了文件无需重启
                now, target, gamma = sched.tick(cfg, tz_override)
                key = target and (target["temp"], target["dim"], target["brightness"])
                if key != last_key:
                    last_key = key
                    if target is None:
                        print(f"[{now:%H:%M:%S}] 时间表为空")
                    else:
                        b = target["brightness"]
                        print(f"[{now:%H:%M:%S} {now:%z}] {target['temp']}K  调暗 {target['dim']}%  "
                              f"硬件亮度 {'-' if b is None else str(b) + '%'}"
                              f"{'  (渐变中)' if target['fading'] else ''}"
                              f"{'' if gamma[0] == gamma[1] else '  [Gamma 设置失败]'}")
                interval = cfg.get("interval_seconds", 30)
            except Exception as e:  # 配置写错时不退出, 等下一轮
                print("错误:", e)
                interval = 30
            time.sleep(max(5, interval))
    except KeyboardInterrupt:
        print("已退出 (当前色温保持不变, 可用 --reset 恢复)")
    finally:
        release_scheduler_lock(lock)


# --------------------------------------------------------------------------- #
# 图形界面
# --------------------------------------------------------------------------- #
def run_gui(cfg_path=CONFIG_PATH):
    import tkinter as tk
    from tkinter import ttk

    try:
        cfg = load_config(cfg_path)
    except Exception as e:
        print("配置文件有误, 使用默认值:", e)
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))

    root = tk.Tk()
    root.title("屏幕亮度 / 色温")
    root.resizable(False, False)
    pad = {"padx": 10, "pady": 4}

    init_b = wmi_get_brightness()
    if init_b is None:
        d = ddc_get_brightness()
        init_b = round(d[0][1] / (d[0][2] or 100) * 100) if d else 100

    hw_var = tk.IntVar(value=init_b)
    temp_var = tk.IntVar(value=6500)
    dim_var = tk.IntVar(value=100)
    status = tk.StringVar(value="就绪")
    pending = {}
    labels = {}

    def debounce(key, ms, fn):
        if key in pending:
            root.after_cancel(pending[key])
        pending[key] = root.after(ms, fn)

    def apply_hw():
        wmi_ok, ddc_ok = set_hw_brightness(hw_var.get())
        if wmi_ok or ddc_ok:
            status.set(f"硬件亮度 {hw_var.get()}%  (内屏:{'✓' if wmi_ok else '-'}  DDC/CI:{ddc_ok} 台)")
        else:
            status.set("硬件亮度调节失败: 显示器不支持 WMI / DDC/CI, 可用「软件调暗」")

    def apply_gamma():
        ok, total = set_gamma(temp_var.get(), dim_var.get())
        if ok == total:
            status.set(f"色温 {temp_var.get()}K, 软件亮度 {dim_var.get()}%")
        else:
            status.set(f"Gamma 设置失败 ({ok}/{total}), 数值可能超出系统限制, 可尝试 --unlock")

    # ---------------- 手动调节 ----------------
    manual = ttk.LabelFrame(root, text="手动调节")
    manual.pack(fill="x", padx=10, pady=(10, 4))

    def row(parent, name, label, var, lo, hi, unit, on_change):
        frm = ttk.Frame(parent)
        frm.pack(fill="x", **pad)
        ttk.Label(frm, text=label, width=10).pack(side="left")
        val = ttk.Label(frm, width=7)
        val.pack(side="right")
        labels[name] = (val, var, unit)

        def changed(_=None):
            var.set(int(float(var.get())))
            val.config(text=f"{var.get()}{unit}")
            on_change()

        ttk.Scale(frm, from_=lo, to=hi, variable=var, length=300,
                  command=changed).pack(side="left", fill="x", expand=True)
        val.config(text=f"{var.get()}{unit}")

    def refresh_labels():
        for val, var, unit in labels.values():
            val.config(text=f"{var.get()}{unit}")

    row(manual, "hw", "硬件亮度", hw_var, 0, 100, "%", lambda: debounce("hw", 300, apply_hw))
    row(manual, "t", "色温", temp_var, 1900, 6500, "K", lambda: debounce("g", 50, apply_gamma))
    row(manual, "d", "软件调暗", dim_var, 30, 100, "%", lambda: debounce("g", 50, apply_gamma))

    btns = ttk.Frame(manual)
    btns.pack(fill="x", **pad)
    for text, k in (("夜间 3400K", 3400), ("暖色 4500K", 4500), ("默认 6500K", 6500)):
        ttk.Button(btns, text=text,
                   command=lambda k=k: (temp_var.set(k), refresh_labels(), apply_gamma())
                   ).pack(side="left", padx=4)

    def reset():
        temp_var.set(6500)
        dim_var.set(100)
        refresh_labels()
        apply_gamma()

    ttk.Button(btns, text="重置", command=reset).pack(side="right", padx=4)

    # ---------------- 定时 ----------------
    sched_box = ttk.LabelFrame(root, text="定时")
    sched_box.pack(fill="x", padx=10, pady=4)

    top = ttk.Frame(sched_box)
    top.pack(fill="x", **pad)
    ttk.Label(top, text="时区").pack(side="left")
    tz_var = tk.StringVar(value=cfg["timezone"])
    ttk.Combobox(top, textvariable=tz_var, values=tz_choices(), width=18).pack(side="left", padx=6)
    ttk.Label(top, text="渐变(分钟)").pack(side="left", padx=(12, 0))
    fade_var = tk.StringVar(value=str(cfg["fade_minutes"]))
    ttk.Spinbox(top, from_=0, to=240, increment=5, textvariable=fade_var, width=5
                ).pack(side="left", padx=6)
    clock = tk.StringVar()
    ttk.Label(top, textvariable=clock, foreground="#06c").pack(side="right")

    cols = (("time", "时间", 70), ("temp", "色温", 80), ("dim", "软件调暗", 80), ("bri", "硬件亮度", 80))
    tree = ttk.Treeview(sched_box, columns=[c[0] for c in cols], show="headings", height=6)
    for cid, text, w in cols:
        tree.heading(cid, text=text)
        tree.column(cid, width=w, anchor="center")
    tree.pack(fill="x", **pad)

    points = [dict(p) for p in cfg["points"]]

    def refresh_tree():
        points.sort(key=lambda p: parse_hhmm(p["time"]))
        tree.delete(*tree.get_children())
        for p in points:
            b = p.get("brightness")
            tree.insert("", "end", iid=p["time"], values=(
                p["time"], f"{p['temp']}K", f"{p['dim']}%", "不调" if b is None else f"{b}%"))

    edit = ttk.Frame(sched_box)
    edit.pack(fill="x", **pad)
    e_vars = {}
    for key, text, w, default in (("time", "时间", 6, "21:00"), ("temp", "色温", 6, "4000"),
                                  ("dim", "调暗", 4, "100"), ("brightness", "亮度", 4, "")):
        ttk.Label(edit, text=text).pack(side="left")
        e_vars[key] = tk.StringVar(value=default)
        ttk.Entry(edit, textvariable=e_vars[key], width=w).pack(side="left", padx=(2, 8))

    def on_select(_=None):
        sel = tree.selection()
        if not sel:
            return
        p = next(p for p in points if p["time"] == sel[0])
        for k in e_vars:
            v = p.get(k)
            e_vars[k].set("" if v is None else str(v))

    tree.bind("<<TreeviewSelect>>", on_select)

    def add_point():
        try:
            p = normalize_point({k: v.get().strip() for k, v in e_vars.items()})
        except ValueError as e:
            status.set(f"无法添加: {e}")
            return
        points[:] = [q for q in points if q["time"] != p["time"]] + [p]
        refresh_tree()
        status.set(f"已添加/更新 {p['time']} (记得点「保存」)")
        if enabled_var.get():
            sched_tick(reschedule=False)

    def delete_point():
        for iid in tree.selection():
            points[:] = [q for q in points if q["time"] != iid]
        refresh_tree()

    ttk.Button(edit, text="添加/更新", command=add_point).pack(side="left", padx=2)
    ttk.Button(edit, text="删除选中", command=delete_point).pack(side="left", padx=2)
    ttk.Label(sched_box, text="提示: 亮度留空 = 该时段不改硬件亮度; 到点后在「渐变」分钟内平滑过渡",
              foreground="#777").pack(fill="x", padx=10)

    def current_cfg():
        fade = fade_var.get().strip()
        return {
            "timezone": tz_var.get().strip() or "local",
            "fade_minutes": int(fade) if fade.isdigit() else 0,
            "interval_seconds": cfg.get("interval_seconds", 30),
            "points": [dict(p) for p in points],
        }

    def save():
        c = current_cfg()
        try:
            parse_tz(c["timezone"])
        except ValueError as e:
            status.set(str(e))
            return False
        save_config(c, cfg_path)
        status.set(f"已保存到 {cfg_path}")
        return True

    bottom = ttk.Frame(sched_box)
    bottom.pack(fill="x", **pad)
    enabled_var = tk.BooleanVar(value=False)
    auto_var = tk.BooleanVar(value=autostart_enabled())
    scheduler = Scheduler(hw_async=True)
    state = {"job": None, "lock": None}

    def sched_tick(reschedule=True):
        if not enabled_var.get():
            return
        c = current_cfg()
        try:
            now, target, gamma = scheduler.tick(c)
        except ValueError as e:
            status.set(f"定时出错: {e}")
            target = None
        else:
            if target is None:
                status.set("时间表为空")
            else:
                temp_var.set(target["temp"])
                dim_var.set(target["dim"])
                if target["brightness"] is not None:
                    hw_var.set(target["brightness"])
                refresh_labels()
                msg = (f"[定时] {now:%H:%M} 时段 {target['point']}: {target['temp']}K, "
                       f"调暗 {target['dim']}%")
                if target["fading"]:
                    msg += " (渐变中)"
                if gamma[0] != gamma[1]:
                    msg += " [Gamma 失败]"
                status.set(msg)
        if reschedule:
            state["job"] = root.after(int(max(5, c["interval_seconds"]) * 1000), sched_tick)

    def toggle_enabled():
        if enabled_var.get():
            state["lock"] = acquire_scheduler_lock()
            if state["lock"] is None:
                enabled_var.set(False)
                status.set("后台已有定时实例在运行 (开机自启?), 为避免冲突未启用")
                return
            scheduler.last_hw = None
            sched_tick()
        else:
            if state["job"]:
                root.after_cancel(state["job"])
                state["job"] = None
            release_scheduler_lock(state["lock"])
            state["lock"] = None
            status.set("定时已停止 (手动调节恢复生效)")

    def toggle_autostart():
        try:
            if auto_var.get():
                if not save():
                    auto_var.set(False)
                    return
                set_autostart(True, cfg_path)
                status.set("已设置开机自启 (下次登录生效, 后台无窗口运行)")
            else:
                set_autostart(False)
                status.set("已取消开机自启")
        except OSError as e:
            status.set(f"设置开机自启失败: {e}")

    ttk.Checkbutton(bottom, text="启用定时", variable=enabled_var,
                    command=toggle_enabled).pack(side="left")
    ttk.Checkbutton(bottom, text="开机自启", variable=auto_var,
                    command=toggle_autostart).pack(side="left", padx=12)
    ttk.Button(bottom, text="保存", command=save).pack(side="right")

    def update_clock():
        try:
            now = now_in_tz(parse_tz(tz_var.get()))
            z = now.strftime("%z")
            clock.set(f"{now:%H:%M:%S}  UTC{z[:3]}:{z[3:5]}")
        except ValueError:
            clock.set("时区无效")
        root.after(1000, update_clock)

    refresh_tree()
    update_clock()
    ttk.Label(root, textvariable=status, foreground="#555", wraplength=420
              ).pack(fill="x", padx=10, pady=(4, 10))
    root.mainloop()


# --------------------------------------------------------------------------- #
# 命令行
# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description="Windows 屏幕亮度 / 色温调节")
    p.add_argument("-b", "--brightness", type=int, metavar="0-100", help="硬件亮度 (WMI / DDC/CI)")
    p.add_argument("-t", "--temp", type=int, metavar="K", help="色温 1900~6500, 默认 6500")
    p.add_argument("-d", "--dim", type=int, metavar="10-100", help="软件调暗 (Gamma), 默认 100")
    p.add_argument("--reset", action="store_true", help="恢复默认 Gamma (6500K, 100%%)")
    p.add_argument("--info", action="store_true", help="显示显示器信息与当前亮度")
    p.add_argument("--gui", action="store_true", help="打开图形界面")
    p.add_argument("--unlock", action="store_true", help="(管理员) 解除 Gamma 范围限制")
    p.add_argument("--schedule", action="store_true", help="按时间表持续运行")
    p.add_argument("--show-schedule", action="store_true", help="显示时间表和此刻目标值")
    p.add_argument("--tz", metavar="TZ", help="时区, 如 local / +8 / UTC-05:30 / Asia/Shanghai")
    p.add_argument("--config", metavar="FILE", default=str(CONFIG_PATH), help="时间表配置文件路径")
    p.add_argument("--autostart", choices=["on", "off"], help="开机自动运行定时")
    args = p.parse_args()
    cfg_path = Path(args.config)

    if len(sys.argv) == 1 or args.gui:
        run_gui(cfg_path)
        return

    if args.tz is not None:
        try:
            parse_tz(args.tz)
        except ValueError as e:
            sys.exit(str(e))

    if args.unlock:
        try:
            unlock_gamma_range()
            print("已写入注册表 GdiIcmGammaRange=256, 注销或重启后生效")
        except PermissionError:
            print("需要以管理员身份运行")

    if args.info:
        for _, dev in enum_monitors():
            print("显示器:", dev)
        w = wmi_get_brightness()
        print("内置屏亮度 (WMI):", f"{w}%" if w is not None else "不支持")
        for desc, cur, mx in ddc_get_brightness():
            print(f"DDC/CI [{desc}]: {cur}/{mx}")

    if args.brightness is not None:
        wmi_ok, ddc_ok = set_hw_brightness(args.brightness)
        if wmi_ok or ddc_ok:
            print(f"硬件亮度已设为 {args.brightness}% (内屏: {'成功' if wmi_ok else '无'}, DDC/CI: {ddc_ok} 台)")
        else:
            print("硬件亮度设置失败: 显示器不支持 WMI / DDC/CI, 可改用 -d 软件调暗")

    if args.reset or args.temp is not None or args.dim is not None:
        kelvin = 6500 if args.reset else (args.temp or 6500)
        dim = 100 if args.reset else (args.dim or 100)
        ok, total = set_gamma(kelvin, dim)
        if ok == total:
            print(f"色温 {kelvin}K, 软件亮度 {dim}%")
        else:
            print(f"Gamma 设置失败 ({ok}/{total}): 数值可能超出 Windows 允许范围, "
                  f"可以管理员身份运行 --unlock 后重启")

    if args.autostart:
        if args.autostart == "on" and not cfg_path.exists():
            save_config(DEFAULT_CONFIG, cfg_path)
        set_autostart(args.autostart == "on", cfg_path.resolve())
        print("已开启开机自启" if args.autostart == "on" else "已关闭开机自启")

    if args.show_schedule:
        if not cfg_path.exists():
            save_config(DEFAULT_CONFIG, cfg_path)
            print(f"未找到配置, 已生成默认时间表: {cfg_path}")
        cfg = load_config(cfg_path)
        tz_text = args.tz if args.tz is not None else cfg["timezone"]
        now = now_in_tz(parse_tz(tz_text))
        print(f"时区: {tz_text}   当前时间: {now:%Y-%m-%d %H:%M:%S %z}   渐变: {cfg['fade_minutes']} 分钟")
        for pt in cfg["points"]:
            b = pt["brightness"]
            print(f"  {pt['time']}  {pt['temp']}K  调暗 {pt['dim']}%  "
                  f"硬件亮度 {'不调' if b is None else str(b) + '%'}")
        t = compute_target(cfg, now)
        if t:
            print(f"此刻目标: {t['temp']}K  调暗 {t['dim']}%  硬件亮度 "
                  f"{'不调' if t['brightness'] is None else str(t['brightness']) + '%'}"
                  f"{'  (渐变中)' if t['fading'] else ''}")

    if args.schedule:
        if not cfg_path.exists():
            save_config(DEFAULT_CONFIG, cfg_path)
            print(f"未找到配置, 已生成默认时间表: {cfg_path}")
        run_schedule_loop(cfg_path, args.tz)


if __name__ == "__main__":
    main()
