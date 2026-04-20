"""
หน้าต่างเลือกหมวดข่าว — แสดงผลในโปรแกรม + เปิดด้วยดับเบิลคลิกผ่าน เปิดข่าวรายวัน.bat

  python gui.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

from brief import ROOT, load_config, merged_options, run_brief

OUTPUT_DIR = ROOT / "output"


class NewsBriefApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Daily News Brief — ข่าวรายวัน")
        self.root.minsize(560, 520)
        self.root.geometry("820x680")

        self.section_vars: dict[str, tk.BooleanVar] = {}
        self.cfg: dict = {}
        self.opts: dict = {}
        self._last_output_path: Path | None = None

        try:
            self.cfg = load_config()
            self.opts = merged_options(self.cfg)
        except FileNotFoundError as e:
            messagebox.showerror("ไม่พบไฟล์", f"ไม่พบ feeds.json:\n{e}")
            raise SystemExit(1) from e

        self._build()
        self._load_latest_output()

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 4}

        outer = ttk.Panedwindow(self.root, orient=tk.VERTICAL)
        outer.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        top = ttk.Frame(outer)
        bottom = ttk.Frame(outer)
        outer.add(top, weight=0)
        outer.add(bottom, weight=1)

        frm_top = ttk.Frame(top)
        frm_top.pack(fill=tk.X, **pad)
        ttk.Label(
            frm_top,
            text="เลือกหมวด แล้วกดสร้าง — ผลลัพธ์จะแสดงด้านล่าง",
            wraplength=760,
        ).pack(anchor=tk.W)

        sections = self.cfg.get("sections") or {}
        frm_sec = ttk.LabelFrame(top, text="หมวดข่าว")
        frm_sec.pack(fill=tk.BOTH, expand=True, **pad)

        self.var_all = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frm_sec,
            text="ทั้งหมด (ทุกหมวด)",
            variable=self.var_all,
            command=self._on_toggle_all,
        ).pack(anchor=tk.W, padx=8, pady=4)

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

        frm_opt = ttk.LabelFrame(top, text="ตัวเลือก")
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

        frm_btn = ttk.Frame(top)
        frm_btn.pack(fill=tk.X, **pad)

        self.btn_run = ttk.Button(frm_btn, text="สร้างข่าวตอนนี้", command=self._run_clicked)
        self.btn_run.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(frm_btn, text="โหลดไฟล์ล่าสุด", command=self._load_latest_output).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(frm_btn, text="เปิดโฟลเดอร์ผลลัพธ์", command=self._open_output_folder).pack(
            side=tk.LEFT
        )

        self.status = tk.StringVar(value="พร้อม")
        ttk.Label(top, textvariable=self.status, foreground="#333").pack(fill=tk.X, padx=4, pady=(4, 0))

        out_lf = ttk.LabelFrame(bottom, text="ผลลัพธ์ (Markdown)")
        out_lf.pack(fill=tk.BOTH, expand=True)

        self.result_text = scrolledtext.ScrolledText(
            out_lf,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            undo=True,
            height=18,
        )
        self.result_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

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
        return [k for k, v in self.section_vars.items() if v.get()]

    def _show_file(self, path: Path) -> None:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError as e:
            self.status.set(f"อ่านไฟล์ไม่ได้: {e}")
            return
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert("1.0", body)
        self.result_text.see("1.0")
        self._last_output_path = path.resolve()
        self.status.set(f"แสดง: {path.name}")

    def _load_latest_output(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        candidates = list(OUTPUT_DIR.glob("*.md"))
        if not candidates:
            self.status.set("ยังไม่มีไฟล์ในโฟลเดอร์ output")
            return
        latest = max(candidates, key=lambda p: p.stat().st_mtime)
        self._show_file(latest)

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
                out_path, errors = run_brief(
                    section_keys=section_keys,
                    translate=translate,
                    fetch_body=fetch_body,
                    out_path=None,
                )
                n_err = len(errors)
                self.root.after(
                    0,
                    lambda p=out_path, n=n_err: self._run_done(True, p, n, None),
                )
            except Exception as e:
                err_s = str(e)
                self.root.after(0, lambda s=err_s: self._run_done(False, None, 0, s))

        threading.Thread(target=worker, daemon=True).start()

    def _run_done(
        self,
        ok: bool,
        out_path: Path | None,
        warn_count: int,
        err: str | None,
    ) -> None:
        self.btn_run.config(state=tk.NORMAL)
        if ok and out_path is not None:
            self._show_file(out_path)
            msg = "สร้างข่าวเสร็จแล้ว"
            if warn_count:
                msg += f" (มีคำเตือน {warn_count} รายการในไฟล์)"
            self.status.set(msg)
            messagebox.showinfo("เสร็จแล้ว", msg)
        elif not ok and err:
            self.status.set("เกิดข้อผิดพลาด")
            messagebox.showerror("ผิดพลาด", err)
        else:
            self.status.set("พร้อม")

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    NewsBriefApp().run()


if __name__ == "__main__":
    main()
