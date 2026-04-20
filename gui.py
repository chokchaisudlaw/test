"""
หน้าต่างเลือกหมวดข่าวแล้วกดสร้าง — ใช้ Tkinter (มีในตัว Python)

  python gui.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from brief import ROOT, load_config, merged_options, run_brief

OUTPUT_DIR = ROOT / "output"


class NewsBriefApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Daily News Brief — เลือกหมวด")
        self.root.minsize(420, 360)
        self.root.geometry("480x420")

        self.section_vars: dict[str, tk.BooleanVar] = {}
        self.cfg: dict = {}
        self.opts: dict = {}

        try:
            self.cfg = load_config()
            self.opts = merged_options(self.cfg)
        except FileNotFoundError as e:
            messagebox.showerror("ไม่พบไฟล์", f"ไม่พบ feeds.json:\n{e}")
            raise SystemExit(1) from e

        self._build()

    def _build(self) -> None:
        pad = {"padx": 12, "pady": 6}

        frm_top = ttk.Frame(self.root)
        frm_top.pack(fill=tk.X, **pad)
        ttk.Label(
            frm_top,
            text="เลือกหมวดที่ต้องการ แล้วกดสร้างไฟล์ Markdown",
            wraplength=440,
        ).pack(anchor=tk.W)

        sections = self.cfg.get("sections") or {}
        frm_sec = ttk.LabelFrame(self.root, text="หมวดข่าว")
        frm_sec.pack(fill=tk.BOTH, expand=True, **pad)

        self.var_all = tk.BooleanVar(value=True)
        cb_all = ttk.Checkbutton(
            frm_sec,
            text="ทั้งหมด (ทุกหมวด)",
            variable=self.var_all,
            command=self._on_toggle_all,
        )
        cb_all.pack(anchor=tk.W, padx=8, pady=4)

        for key in sections:
            meta = sections[key]
            label = meta.get("title_th") or key
            var = tk.BooleanVar(value=True)
            self.section_vars[key] = var
            ttk.Checkbutton(
                frm_sec,
                text=f"{label}  ({key})",
                variable=var,
                command=self._sync_all_checkbox,
            ).pack(anchor=tk.W, padx=16, pady=2)

        frm_opt = ttk.LabelFrame(self.root, text="ตัวเลือก")
        frm_opt.pack(fill=tk.X, **pad)

        self.var_translate = tk.BooleanVar(value=bool(self.opts.get("translate_to_thai", True)))
        self.var_fetch = tk.BooleanVar(value=bool(self.opts.get("fetch_article_body", True)))

        ttk.Checkbutton(
            frm_opt,
            text="แปลเป็นภาษาไทย",
            variable=self.var_translate,
        ).pack(anchor=tk.W, padx=8, pady=2)
        ttk.Checkbutton(
            frm_opt,
            text="ดึงเนื้อหาเต็มจากลิงก์ (ช้า แต่ละเอียด)",
            variable=self.var_fetch,
        ).pack(anchor=tk.W, padx=8, pady=2)

        frm_btn = ttk.Frame(self.root)
        frm_btn.pack(fill=tk.X, **pad)

        self.btn_run = ttk.Button(frm_btn, text="สร้างข่าวตอนนี้", command=self._run_clicked)
        self.btn_run.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(frm_btn, text="เปิดโฟลเดอร์ผลลัพธ์", command=self._open_output_folder).pack(
            side=tk.LEFT
        )

        self.status = tk.StringVar(value="พร้อม")
        ttk.Label(self.root, textvariable=self.status, foreground="#333").pack(
            fill=tk.X, padx=12, pady=(0, 8)
        )

        self._sync_all_checkbox()

    def _on_toggle_all(self) -> None:
        if self.var_all.get():
            for v in self.section_vars.values():
                v.set(True)
        else:
            for v in self.section_vars.values():
                v.set(False)

    def _sync_all_checkbox(self) -> None:
        if not self.section_vars:
            return
        if all(v.get() for v in self.section_vars.values()):
            self.var_all.set(True)
        elif not any(v.get() for v in self.section_vars.values()):
            self.var_all.set(False)

    def _selected_keys(self) -> list[str]:
        keys = [k for k, v in self.section_vars.items() if v.get()]
        return keys

    def _open_output_folder(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = str(OUTPUT_DIR.resolve())
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)

    def _run_clicked(self) -> None:
        keys = self._selected_keys()
        if not keys:
            messagebox.showwarning("เลือกหมวด", "เลือกอย่างน้อยหนึ่งหมวด")
            return

        translate = self.var_translate.get()
        fetch_body = self.var_fetch.get()

        all_keys = list((self.cfg.get("sections") or {}).keys())
        section_keys: list[str] | None = None
        if set(keys) != set(all_keys):
            section_keys = keys

        self.btn_run.config(state=tk.DISABLED)
        self.status.set("กำลังดึงข่าวและแปล… (อาจใช้เวลานาน)")

        def worker() -> None:
            try:
                path, errors = run_brief(
                    section_keys=section_keys,
                    translate=translate,
                    fetch_body=fetch_body,
                    out_path=None,
                )
                msg = f"สำเร็จ:\n{path}"
                if errors:
                    msg += f"\n\nมีคำเตือน {len(errors)} รายการ (ดูในไฟล์)"
                self.root.after(0, lambda m=msg: self._run_done(True, m))
            except Exception as e:
                err_s = str(e)
                self.root.after(0, lambda s=err_s: self._run_done(False, s))

        threading.Thread(target=worker, daemon=True).start()

    def _run_done(self, ok: bool, message: str) -> None:
        self.btn_run.config(state=tk.NORMAL)
        self.status.set("พร้อม" if ok else "เกิดข้อผิดพลาด")
        if ok:
            messagebox.showinfo("เสร็จแล้ว", message)
        else:
            messagebox.showerror("ผิดพลาด", message)

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    NewsBriefApp().run()


if __name__ == "__main__":
    main()
