from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import threading
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.wechat_key_preparer.core import (  # noqa: E402
    KeyPreparationError,
    default_result_dir,
    prepare_keys,
)


_ERROR_MESSAGES = {
    "WECHAT_NOT_RUNNING": "没有检测到已登录的微信，请先打开微信。",
    "ACCESS_DENIED": "未获得读取权限，请重新启动并允许管理员确认。",
    "DATABASE_NOT_FOUND": "没有找到微信数据库，请先在微信中完成登录。",
    "KEY_NOT_FOUND": "没有找到可验证密钥，请保持微信登录后重试。",
    "PREPARATION_FAILED": "准备失败，请关闭微信更新后重试。",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--result-dir", type=Path, default=default_result_dir())
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        return 2
    if not _is_admin():
        return 0 if _relaunch_elevated(args.result_dir) else 1
    _show_window(args.result_dir)
    return 0


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def _relaunch_elevated(result_dir: Path) -> bool:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        arguments = ["--result-dir", str(result_dir)]
    else:
        python = Path(sys.executable).resolve()
        pythonw = python.with_name("pythonw.exe")
        executable = pythonw if pythonw.is_file() else python
        arguments = [str(Path(__file__).resolve()), "--result-dir", str(result_dir)]
    result = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        str(executable),
        subprocess.list2cmdline(arguments),
        str(executable.parent),
        1,
    )
    return result > 32


def _show_window(result_dir: Path) -> None:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    wm_create = 0x0001
    wm_destroy = 0x0002
    wm_close = 0x0010
    wm_command = 0x0111
    wm_setfont = 0x0030
    wm_ctlcolorstatic = 0x0138
    wm_preparation_result = 0x8001
    button_id = 1001
    ws_child = 0x40000000
    ws_visible = 0x10000000
    ws_tabstop = 0x00010000
    bs_defpushbutton = 0x00000001
    main_window_style = 0x00CA0000
    sw_show = 5
    default_gui_font = 17

    wnd_proc_type = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    class WindowClass(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("window_proc", wnd_proc_type),
            ("class_extra", ctypes.c_int),
            ("window_extra", ctypes.c_int),
            ("instance", wintypes.HINSTANCE),
            ("icon", wintypes.HICON),
            ("cursor", wintypes.HANDLE),
            ("background", wintypes.HBRUSH),
            ("menu_name", wintypes.LPCWSTR),
            ("class_name", wintypes.LPCWSTR),
        ]

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
    user32.RegisterClassW.restype = wintypes.ATOM
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.SetWindowTextW.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.EnableWindow.restype = wintypes.BOOL
    user32.MessageBoxW.argtypes = [
        wintypes.HWND,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.UINT,
    ]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.UpdateWindow.argtypes = [wintypes.HWND]
    user32.UpdateWindow.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    gdi32.GetStockObject.argtypes = [ctypes.c_int]
    gdi32.GetStockObject.restype = wintypes.HGDIOBJ
    gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.SetBkMode.restype = ctypes.c_int

    instance = kernel32.GetModuleHandleW(None)
    white_brush = gdi32.GetStockObject(0)
    class_name = f"ChatboxKeyPreparerWindow{os.getpid()}"
    controls: dict[str, int] = {}
    state = {"running": False, "finished": False}
    error_codes = tuple(_ERROR_MESSAGES)

    def create_control(
        parent: int,
        control_class: str,
        text: str,
        style: int,
        x: int,
        y: int,
        width: int,
        height: int,
        control_id: int = 0,
    ) -> int:
        menu = wintypes.HMENU(control_id) if control_id else None
        handle = user32.CreateWindowExW(
            0,
            control_class,
            text,
            style,
            x,
            y,
            width,
            height,
            parent,
            menu,
            instance,
            None,
        )
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        font = gdi32.GetStockObject(default_gui_font)
        user32.SendMessageW(handle, wm_setfont, font, True)
        return handle

    def set_control_text(name: str, text: str) -> None:
        user32.SetWindowTextW(controls[name], text)

    def preparation_worker(window: int) -> None:
        try:
            count = prepare_keys(result_dir)
        except KeyPreparationError as exc:
            code = exc.code if exc.code in _ERROR_MESSAGES else "PREPARATION_FAILED"
            user32.PostMessageW(window, wm_preparation_result, error_codes.index(code), 0)
        else:
            user32.PostMessageW(window, wm_preparation_result, count, 1)

    @wnd_proc_type
    def window_proc(
        window: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        if message == wm_create:
            controls["heading"] = create_control(
                window,
                "STATIC",
                "准备微信数据库密钥",
                ws_child | ws_visible,
                28,
                24,
                490,
                30,
            )
            controls["privacy"] = create_control(
                window,
                "STATIC",
                "只读访问本机已登录的微信并验证数据库。\r\n"
                "全程离线，不上传、不记录，也不会显示密钥内容。",
                ws_child | ws_visible,
                28,
                68,
                500,
                55,
            )
            controls["status"] = create_control(
                window,
                "STATIC",
                "点击开始后请保持微信登录。处理期间可以继续使用其他窗口。",
                ws_child | ws_visible,
                28,
                145,
                500,
                55,
            )
            controls["button"] = create_control(
                window,
                "BUTTON",
                "开始准备",
                ws_child | ws_visible | ws_tabstop | bs_defpushbutton,
                28,
                225,
                126,
                38,
                button_id,
            )
            return 0
        if message == wm_command and int(wparam) & 0xFFFF == button_id:
            if state["finished"]:
                user32.DestroyWindow(window)
                return 0
            if state["running"]:
                return 0
            state["running"] = True
            user32.EnableWindow(controls["button"], False)
            set_control_text("button", "正在准备")
            set_control_text("status", "正在本机扫描并逐库验证，请不要退出微信……")
            threading.Thread(
                target=preparation_worker,
                args=(window,),
                name="chatbox-key-preparer",
                daemon=True,
            ).start()
            return 0
        if message == wm_preparation_result:
            state["running"] = False
            user32.EnableWindow(controls["button"], True)
            if lparam:
                state["finished"] = True
                set_control_text(
                    "status",
                    f"已准备并验证 {int(wparam)} 个数据库密钥。请返回 Chatbox 检查并导入。",
                )
                set_control_text("button", "关闭")
            else:
                code = error_codes[int(wparam)]
                set_control_text("status", _ERROR_MESSAGES[code])
                set_control_text("button", "重试")
            return 0
        if message == wm_ctlcolorstatic:
            gdi32.SetBkMode(wparam, 1)
            return int(white_brush)
        if message == wm_close:
            if state["running"]:
                user32.MessageBoxW(
                    window,
                    "密钥仍在本机验证。完成后即可关闭窗口。",
                    "Chatbox 微信密钥准备器",
                    0x00000040,
                )
                return 0
            user32.DestroyWindow(window)
            return 0
        if message == wm_destroy:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(window, message, wparam, lparam)

    window_class = WindowClass(
        0,
        window_proc,
        0,
        0,
        instance,
        None,
        user32.LoadCursorW(None, ctypes.c_void_p(32512)),
        white_brush,
        None,
        class_name,
    )
    if not user32.RegisterClassW(ctypes.byref(window_class)):
        raise ctypes.WinError(ctypes.get_last_error())

    width, height = 570, 330
    x = max(0, (user32.GetSystemMetrics(0) - width) // 2)
    y = max(0, (user32.GetSystemMetrics(1) - height) // 2)
    window = user32.CreateWindowExW(
        0,
        class_name,
        "Chatbox 微信密钥准备器",
        main_window_style,
        x,
        y,
        width,
        height,
        None,
        None,
        instance,
        None,
    )
    if not window:
        raise ctypes.WinError(ctypes.get_last_error())
    user32.ShowWindow(window, sw_show)
    user32.UpdateWindow(window)

    message = wintypes.MSG()
    while True:
        result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
        if result == 0:
            break
        if result == -1:
            raise ctypes.WinError(ctypes.get_last_error())
        user32.TranslateMessage(ctypes.byref(message))
        user32.DispatchMessageW(ctypes.byref(message))


if __name__ == "__main__":
    raise SystemExit(main())
