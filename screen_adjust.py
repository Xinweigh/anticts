"""
Windows 屏幕亮度 / 色温调节脚本（纯标准库，无需 pip 安装任何东西）

功能:
  * 硬件亮度: 笔记本内屏走 WMI, 外接显示器走 DDC/CI (需显示器菜单里开启 DDC/CI)
  * 色温:     通过 Gamma 曲线 (SetDeviceGammaRamp) 调整, 1900K ~ 6500K
  * 软件调暗: 通过 Gamma 曲线整体压暗, 适合不支持 DDC/CI 的显示器

用法:
  python screen_adjust.py --gui                 打开滑块窗口
  python screen_adjust.py --info                查看显示器和当前亮度
  python screen_adjust.py -b 60                 硬件亮度设为 60%
  python screen_adjust.py -t 4500               色温设为 4500K
  python screen_adjust.py -t 4000 -d 80         色温 4000K + 软件调暗到 80%
  python screen_adjust.py --reset               恢复默认色温 / Gamma
  python screen_adjust.py --unlock              (管理员) 解除 Windows 对 Gamma 范围的限制
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import math
import subprocess
import sys

if sys.platform != "win32":
    sys.exit("此脚本仅支持 Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dxva2 = ctypes.WinDLL("dxva2", use_last_error=True)


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
# 图形界面
# --------------------------------------------------------------------------- #
def run_gui():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("屏幕亮度 / 色温")
    root.resizable(False, False)
    pad = {"padx": 12, "pady": 6}

    init_b = wmi_get_brightness()
    if init_b is None:
        d = ddc_get_brightness()
        init_b = round(d[0][1] / (d[0][2] or 100) * 100) if d else 100

    hw_var = tk.IntVar(value=init_b)
    temp_var = tk.IntVar(value=6500)
    dim_var = tk.IntVar(value=100)
    status = tk.StringVar(value="就绪")
    pending = {}

    def debounce(key, ms, fn):
        if key in pending:
            root.after_cancel(pending[key])
        pending[key] = root.after(ms, fn)

    def apply_hw():
        wmi_ok, ddc_ok = set_hw_brightness(hw_var.get())
        if wmi_ok or ddc_ok:
            status.set(f"硬件亮度 {hw_var.get()}%  (内屏:{'✓' if wmi_ok else '-'}  DDC/CI:{ddc_ok} 台)")
        else:
            status.set("硬件亮度调节失败: 显示器不支持 WMI / DDC/CI, 可用下方「软件调暗」")

    def apply_gamma():
        ok, total = set_gamma(temp_var.get(), dim_var.get())
        if ok == total:
            status.set(f"色温 {temp_var.get()}K, 软件亮度 {dim_var.get()}%")
        else:
            status.set(f"Gamma 设置失败 ({ok}/{total}), 数值可能超出系统限制, 可尝试 --unlock")

    def row(label, var, lo, hi, unit, on_change):
        frm = ttk.Frame(root)
        frm.pack(fill="x", **pad)
        ttk.Label(frm, text=label, width=10).pack(side="left")
        val = ttk.Label(frm, width=7)
        val.pack(side="right")

        def changed(_=None):
            var.set(int(float(var.get())))
            val.config(text=f"{var.get()}{unit}")
            on_change()

        ttk.Scale(frm, from_=lo, to=hi, variable=var, length=280,
                  command=changed).pack(side="left", fill="x", expand=True)
        val.config(text=f"{var.get()}{unit}")

    row("硬件亮度", hw_var, 0, 100, "%", lambda: debounce("hw", 300, apply_hw))
    row("色温", temp_var, 1900, 6500, "K", lambda: debounce("g", 50, apply_gamma))
    row("软件调暗", dim_var, 30, 100, "%", lambda: debounce("g", 50, apply_gamma))

    btns = ttk.Frame(root)
    btns.pack(fill="x", **pad)
    for text, k in (("夜间 3400K", 3400), ("暖色 4500K", 4500), ("默认 6500K", 6500)):
        ttk.Button(btns, text=text,
                   command=lambda k=k: (temp_var.set(k), apply_gamma())).pack(side="left", padx=4)

    def reset():
        temp_var.set(6500)
        dim_var.set(100)
        apply_gamma()

    ttk.Button(btns, text="重置", command=reset).pack(side="right", padx=4)
    ttk.Label(root, textvariable=status, foreground="#555").pack(fill="x", **pad)
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
    args = p.parse_args()

    if len(sys.argv) == 1 or args.gui:
        run_gui()
        return

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


if __name__ == "__main__":
    main()
