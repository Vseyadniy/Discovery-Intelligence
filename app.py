"""
Discovery Research Launcher — desktop app for the staged research pipeline.

Tabs:
  1 · Quantitative research — discovery → research batches → repair → Build Excel
                              (gate-accepted records only). Two modes: Prompt
                              (paste into ChatGPT) or API (automatic, with live
                              agent status + ETA).
  2 · Qualitative research  — one-pager track on gate-accepted companies of any
                              run (current or past): goal + angle per company →
                              qual prompts → qual gate → final .docx report
                              (executive summary + all one-pagers).
  3 · Settings              — API keys (.env), Excel layout and One-pager layout
                              editors (view / add / edit / delete).

Run it:   python app.py     (or double-click the .app built by build_macos_app.py)
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, ttk

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from src import runs          # noqa: E402
from src import onepager      # noqa: E402
from src import model_router as _mr   # noqa: E402,F401  (side effect: loads .env)


def open_path(p: Path) -> None:
    """Open a file/folder in the OS default app (Excel, Word, Finder, …)."""
    p = str(p)
    if sys.platform == "darwin":
        subprocess.run(["open", p])
    elif sys.platform.startswith("win"):
        os.startfile(p)  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", p])


def save_env(values: dict[str, str]) -> None:
    """Write/update KEY=VALUE pairs in .env, preserving unrelated lines."""
    env = ROOT / ".env"
    lines = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    seen, out = set(), []
    for line in lines:
        key = None
        if "=" in line and not line.strip().startswith("#"):
            key = line.split("=", 1)[0].strip()
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in seen]
    env.write_text("\n".join(out) + "\n", encoding="utf-8")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.run_dir: Path | None = None
        self._poll_id = None
        root.title("Discovery Research Launcher")
        root.geometry("880x780")
        self.depths = runs.load_depths()

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)
        tab1 = ttk.Frame(nb, padding=12)
        tab2 = ttk.Frame(nb, padding=12)
        tab3 = ttk.Frame(nb, padding=12)
        nb.add(tab1, text="  1 · Quantitative research  ")
        nb.add(tab2, text="  2 · Qualitative research  ")
        nb.add(tab3, text="  Settings  ")

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w").pack(
            fill="x", side="bottom")

        # the ⚡ provider is shared by tab 1 (picker at the launch button) and
        # Settings (default + keys); one variable keeps them in sync
        _mode = os.environ.get("AGENT_MODE", "gpt")
        self.provider = tk.StringVar(value=_mode if _mode in ("gpt", "claude", "grok", "deepseek") else "gpt")

        self._build_run_tab(tab1)
        self._build_qual_tab(tab2)
        self._build_settings_tab(tab3)

    # ── shared: past-run picker ───────────────────────────────────────────────
    def _pick_run(self, on_picked):
        all_runs = runs.list_runs()
        if not all_runs:
            messagebox.showinfo("No runs", "No runs in logs/ yet.")
            return
        win = tk.Toplevel(self.root)
        win.title("Load past run")
        lb = tk.Listbox(win, width=72, height=min(15, len(all_runs)))
        for m in all_runs:
            lb.insert("end", f"{m['run_id']}  ·  {m['status']}")
        lb.pack(padx=10, pady=10)

        def pick():
            sel = lb.curselection()
            if sel:
                self.run_dir = runs.run_dir_for(all_runs[sel[0]]["run_id"])
                win.destroy()
                on_picked()
        ttk.Button(win, text="Open", command=pick).pack(pady=6)

    # ══ tab 1 · quantitative research ═════════════════════════════════════════
    def _build_run_tab(self, frm: ttk.Frame):
        pad = dict(padx=10, pady=4)
        ttk.Label(frm, text="1 · Configure the run", font=("", 13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", **pad)

        ttk.Label(frm, text="Market").grid(row=1, column=0, sticky="w", **pad)
        self.market = tk.StringVar()
        ttk.Entry(frm, textvariable=self.market, width=48).grid(
            row=1, column=1, columnspan=2, sticky="we", **pad)
        self.model = tk.StringVar(value="chatgpt")   # kept for run metadata

        ttk.Label(frm, text="Depth").grid(row=2, column=0, sticky="nw", **pad)
        self.depth = tk.StringVar(value="medium")
        depth_frame = ttk.Frame(frm)
        depth_frame.grid(row=2, column=1, columnspan=2, sticky="w", **pad)
        for i, (key, d) in enumerate(self.depths.items()):
            ttk.Radiobutton(depth_frame, text=d["label"], value=key,
                            variable=self.depth).grid(row=i, column=0, sticky="w")

        ttk.Label(frm, text="Mode").grid(row=3, column=0, sticky="w", **pad)
        mode_frame = ttk.Frame(frm)
        mode_frame.grid(row=3, column=1, columnspan=2, sticky="w", **pad)
        self.mode = tk.StringVar(value="prompt")
        ttk.Radiobutton(mode_frame, text="Prompt — paste into ChatGPT (no key needed)",
                        value="prompt", variable=self.mode,
                        command=self._apply_mode).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Radiobutton(mode_frame, text="API — automatic ⚡ (keys in Settings)",
                        value="api", variable=self.mode,
                        command=self._apply_mode).grid(row=0, column=1, sticky="w")

        bf = ttk.Frame(frm)
        bf.grid(row=4, column=0, sticky="w", **pad)
        ttk.Label(bf, text="Companies / step:").grid(row=0, column=0)
        self.batch = tk.IntVar(value=3)
        ttk.Spinbox(bf, from_=1, to=5, textvariable=self.batch, width=3).grid(
            row=0, column=1, padx=4)
        ttk.Button(frm, text="Generate run  ▶", command=self.on_generate).grid(
            row=4, column=1, sticky="w", **pad)
        ttk.Button(frm, text="Load past run…",
                   command=lambda: self._pick_run(self._run_loaded)).grid(
            row=4, column=2, sticky="w", **pad)

        ttk.Separator(frm, orient="horizontal").grid(
            row=5, column=0, columnspan=3, sticky="we", pady=8)

        self.step_hdr = ttk.Label(frm, text="2 · Current step", font=("", 13, "bold"))
        self.step_hdr.grid(row=6, column=0, columnspan=2, sticky="w", **pad)
        # diagnostics is mode-independent and NEVER advances the run: it only
        # refreshes run_summary.md and builds a paste-into-ChatGPT prompt
        ttk.Button(frm, text="🩺 Diagnostics", command=self.on_diagnostics).grid(
            row=6, column=2, sticky="e", **pad)
        self.prompt_txt = tk.Text(frm, height=10, width=96, wrap="word")
        self.prompt_txt.grid(row=7, column=0, columnspan=3, sticky="we", **pad)
        self.prompt_txt.configure(state="disabled")

        # two button rows — one per mode, toggled by _apply_mode
        self.prompt_btns = ttk.Frame(frm)
        self.prompt_btns.grid(row=8, column=0, columnspan=3, sticky="w", **pad)
        ttk.Button(self.prompt_btns, text="Copy prompt", command=self.on_copy).grid(
            row=0, column=0, padx=4)
        ttk.Button(self.prompt_btns, text="Next prompt ▶", command=self.on_next).grid(
            row=0, column=1, padx=4)
        ttk.Button(self.prompt_btns, text="Open research folder", command=self.on_open_folder).grid(
            row=0, column=2, padx=4)

        self.api_btns = ttk.Frame(frm)
        self.api_btns.grid(row=8, column=0, columnspan=3, sticky="w", **pad)
        self.api_start_btn = ttk.Button(self.api_btns, text="Start next step  ⚡",
                                        command=self.on_api_step)
        self.api_start_btn.grid(row=0, column=0, padx=4)
        ttk.Label(self.api_btns, text="with:").grid(row=0, column=1, padx=(10, 2))
        self.provider_label = tk.StringVar()
        self.api_model_box = ttk.Combobox(self.api_btns, textvariable=self.provider_label,
                                          state="readonly", width=30,
                                          postcommand=self._refresh_provider_labels)
        self.api_model_box.grid(row=0, column=2, padx=2)
        self.api_model_box.bind("<<ComboboxSelected>>", self._on_provider_picked)
        self._refresh_provider_labels()
        ttk.Button(self.api_btns, text="Open research folder", command=self.on_open_folder).grid(
            row=0, column=3, padx=8)
        self.agent_lbl = ttk.Label(frm, text="", foreground="#0a6", wraplength=820,
                                   justify="left")
        self.agent_lbl.grid(row=9, column=0, columnspan=3, sticky="w", **pad)

        ttk.Separator(frm, orient="horizontal").grid(
            row=10, column=0, columnspan=3, sticky="we", pady=8)

        ttk.Label(frm, text="3 · Progress & deliverable", font=("", 13, "bold")).grid(
            row=11, column=0, columnspan=3, sticky="w", **pad)
        pr = ttk.Frame(frm)
        pr.grid(row=12, column=0, columnspan=3, sticky="we", **pad)
        self.bar = ttk.Progressbar(pr, length=520, mode="determinate", maximum=100)
        self.bar.grid(row=0, column=0, sticky="w")
        self.eta_lbl = ttk.Label(pr, text="", foreground="#666")
        self.eta_lbl.grid(row=0, column=1, sticky="w", padx=10)
        self.progress_lbl = ttk.Label(frm, text="—")
        self.progress_lbl.grid(row=13, column=0, columnspan=3, sticky="w", **pad)

        act = ttk.Frame(frm)
        act.grid(row=14, column=0, columnspan=3, sticky="w", **pad)
        self.build_btn = ttk.Button(act, text="Build Excel", command=self.on_build, state="disabled")
        self.build_btn.grid(row=0, column=0, padx=4)
        self.open_xlsx_btn = ttk.Button(act, text="Open Excel", command=self.on_open_xlsx, state="disabled")
        self.open_xlsx_btn.grid(row=0, column=1, padx=4)
        self.open_analysis_btn = ttk.Button(act, text="Open analysis", command=self.on_open_analysis, state="disabled")
        self.open_analysis_btn.grid(row=0, column=2, padx=4)
        self.gate_btn = ttk.Button(act, text="Gate report", command=self.on_open_gate, state="disabled")
        self.gate_btn.grid(row=0, column=3, padx=4)
        self.publish_btn = ttk.Button(act, text="Publish to docs/ →", command=self.on_publish, state="disabled")
        self.publish_btn.grid(row=0, column=4, padx=4)
        frm.columnconfigure(1, weight=1)
        self._apply_mode()

    def _provider_options(self) -> dict[str, str]:
        """Display label → provider id, with the currently configured models."""
        return {
            f"ChatGPT — {os.environ.get('CHEAP_MODEL', 'gpt-5.5')} (OpenAI)": "gpt",
            f"Claude — {os.environ.get('CLAUDE_MODEL', 'claude-opus-4-8')} (Anthropic)": "claude",
            f"Grok — {os.environ.get('GROK_MODEL', 'grok-4.20-0309-reasoning')} (xAI)": "grok",
            f"DeepSeek — {os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')} (app web tools · full)": "deepseek",
        }

    def _refresh_provider_labels(self):
        opts = self._provider_options()
        for box in (getattr(self, "api_model_box", None),
                    getattr(self, "qual_model_box", None)):
            if box is not None:
                box.configure(values=list(opts))
        current = {v: k for k, v in opts.items()}.get(self.provider.get())
        if current:
            self.provider_label.set(current)

    def _on_provider_picked(self, _ev=None):
        mode = self._provider_options().get(self.provider_label.get())
        if mode:
            self.provider.set(mode)     # Settings' default picker follows along

    def _apply_mode(self):
        if self.mode.get() == "api":
            self.prompt_btns.grid_remove()
            self.api_btns.grid()
            self.step_hdr.configure(text="2 · Current step — agents run automatically ⚡")
        else:
            self.api_btns.grid_remove()
            self.prompt_btns.grid()
            self.agent_lbl.configure(text="")
            self.eta_lbl.configure(text="")
            self.step_hdr.configure(text="2 · Current step — paste this prompt into ChatGPT")

    def _run_loaded(self):
        self._show_prompt((self.run_dir / "prompt.md").read_text(encoding="utf-8"))
        self.status.set(f"Loaded {self.run_dir.name}")
        self.build_btn.configure(state="normal")
        if (self.run_dir / "gate_report.md").exists():
            self.gate_btn.configure(state="normal")
        self._start_poll()

    # ── tab-1 actions ────────────────────────────────────────────────────────
    def on_generate(self):
        market = self.market.get().strip()
        if not market:
            messagebox.showwarning("Market required", "Type the market to research.")
            return
        self.run_dir = runs.create_run(market, self.depth.get(), self.model.get())
        self._show_prompt((self.run_dir / "prompt.md").read_text(encoding="utf-8"))
        nxt = "press «Start next step ⚡»" if self.mode.get() == "api" \
            else "paste the discovery prompt, then «Next prompt ▶»"
        self.status.set(f"Run created: {self.run_dir.name} — {nxt}")
        self.build_btn.configure(state="normal")
        self.open_xlsx_btn.configure(state="disabled")
        self.open_analysis_btn.configure(state="disabled")
        self.gate_btn.configure(state="disabled")
        self._start_poll()

    def on_next(self):
        if not self.run_dir:
            return
        self.status.set("Checking run state (gate + cohort)…")

        def work():
            try:
                kind, text = runs.next_prompt(self.run_dir, self.batch.get())

                def done():
                    self._show_prompt(text)
                    self.status.set(f"Step: {kind} — prompt ready (saved to prompt.md)")
                    if (self.run_dir / "gate_report.md").exists():
                        self.gate_btn.configure(state="normal")
                self.root.after(0, done)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showwarning("Next prompt failed", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def on_api_step(self):
        if not self.run_dir:
            messagebox.showinfo("No run", "Generate or load a run first.")
            return
        provider = self.provider.get()
        self.api_start_btn.configure(state="disabled")
        self.agent_lbl.configure(text=f"⚡ starting ({provider})…")

        def log(msg):
            self.root.after(0, lambda: (self.agent_lbl.configure(text=msg),
                                        self.status.set(msg)))

        def work():
            try:
                from src import api_runner
                summary = api_runner.run_next_step(self.run_dir, self.batch.get(),
                                                   provider, log=log)
                kind, text = runs.next_prompt(self.run_dir, self.batch.get())

                def done():
                    self._show_prompt(text)
                    self.agent_lbl.configure(text=f"✔ {summary}")
                    self.status.set(f"API step done · next: {kind}")
                    self.api_start_btn.configure(state="normal")
                self.root.after(0, done)
            except Exception as e:
                def fail():
                    self.api_start_btn.configure(state="normal")
                    self.agent_lbl.configure(text=f"✖ {e}")
                    messagebox.showwarning(
                        "API step failed",
                        f"{e}\n\nCheck the API keys in Settings (or switch to Prompt mode).")
                self.root.after(0, fail)
        threading.Thread(target=work, daemon=True).start()

    def on_copy(self):
        if not self.run_dir:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append((self.run_dir / "prompt.md").read_text(encoding="utf-8"))
        self.status.set("Prompt copied to clipboard.")

    def on_open_folder(self):
        if not self.run_dir:
            return
        dest = runs.docs_dir_for(self.run_dir)
        dest.mkdir(parents=True, exist_ok=True)
        open_path(dest)

    def on_diagnostics(self):
        """Manual diagnostics: refresh the telemetry summary, show + copy the
        ready-to-paste ChatGPT prompt, reveal the run folder. Independent of
        the research state machine — nothing is advanced, nothing is sent."""
        if not self.run_dir:
            self.status.set("Diagnostics needs a run — create one or «Load past run…» first.")
            return
        try:
            text = runs.diagnostics_prompt(self.run_dir)
        except Exception as e:
            self.status.set(f"Diagnostics failed: {e}")
            return
        self._show_prompt(text)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        open_path(self.run_dir)     # run_summary.md · diagnostics_prompt.md · events.jsonl
        self.status.set("🩺 Diagnostics prompt copied — paste it into ChatGPT. Run folder "
                        "opened (run_summary.md · diagnostics_prompt.md · events.jsonl).")

    def on_build(self):
        if not self.run_dir:
            return
        self.status.set("Building Excel…")

        def work():
            try:
                xlsx = runs.build_excel(self.run_dir)
                runs.analyze(self.run_dir)
                self.root.after(0, lambda: self._built(xlsx))
            except SystemExit as e:
                self.root.after(0, lambda: messagebox.showwarning("Nothing to build", str(e)))
                self.root.after(0, lambda: self.status.set("Build blocked — see gate report."))
        threading.Thread(target=work, daemon=True).start()

    def _built(self, xlsx: Path):
        self.status.set(f"Built {xlsx.name} + analysis.md (gate-accepted records only)")
        self.open_xlsx_btn.configure(state="normal")
        self.open_analysis_btn.configure(state="normal")
        self.gate_btn.configure(state="normal")
        self.publish_btn.configure(state="normal")

    def on_publish(self):
        if not self.run_dir:
            return
        self.status.set("Publishing final deliverable to docs/…")

        def work():
            try:
                dest = runs.publish_run(self.run_dir)
                self.root.after(0, lambda: (self.status.set(f"Published → {dest}"), open_path(dest)))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showwarning("Publish failed", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def on_open_xlsx(self):
        if not self.run_dir:
            return
        meta = runs._load_meta(self.run_dir)
        xlsx = Path(meta.get("xlsx") or "")
        if not xlsx.exists():                       # legacy runs built before renaming
            xlsx = self.run_dir / "research_table.xlsx"
        open_path(xlsx)

    def on_open_analysis(self):
        if self.run_dir:
            open_path(self.run_dir / "analysis.md")

    def on_open_gate(self):
        if self.run_dir and (self.run_dir / "gate_report.md").exists():
            open_path(self.run_dir / "gate_report.md")

    # ══ tab 2 · qualitative research ═════════════════════════════════════════
    def _build_qual_tab(self, frm: ttk.Frame):
        pad = dict(padx=10, pady=4)
        ttk.Label(frm, text="Research goal — what decision does this research serve?",
                  font=("", 12, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", **pad)
        self.goal_txt = tk.Text(frm, height=2, width=96, wrap="word")
        self.goal_txt.grid(row=1, column=0, columnspan=4, sticky="we", **pad)

        ttk.Label(frm, text="Companies · run-backed (gate-accepted) and ✍ manual targets "
                            "· confirm the angle per company",
                  font=("", 12, "bold")).grid(row=2, column=0, columnspan=4, sticky="w", **pad)
        self.qual_lb = tk.Listbox(frm, width=76, height=9, selectmode="extended")
        self.qual_lb.grid(row=3, column=0, columnspan=3, sticky="we", **pad)
        side = ttk.Frame(frm)
        side.grid(row=3, column=3, sticky="nw", **pad)
        ttk.Button(side, text="Load past run…",
                   command=lambda: self._pick_run(self.on_qual_load)).grid(
            row=0, column=0, sticky="we", pady=2)
        ttk.Button(side, text="Load companies", command=self.on_qual_load).grid(
            row=1, column=0, sticky="we", pady=2)
        self.angle_var = tk.StringVar(value="competitor")
        ttk.Combobox(side, textvariable=self.angle_var, state="readonly",
                     values=list(onepager.ANGLES), width=17).grid(row=2, column=0, pady=2)
        ttk.Button(side, text="Set angle → selected", command=self.on_qual_set_angle).grid(
            row=3, column=0, sticky="we", pady=2)
        # manual targets — coexist with run-backed rows (✍ marks them)
        ttk.Button(side, text="➕ Add company…", command=self.on_qual_add_manual).grid(
            row=4, column=0, sticky="we", pady=(8, 2))
        ttk.Button(side, text="✎ Edit selected", command=self.on_qual_edit_manual).grid(
            row=5, column=0, sticky="we", pady=2)
        ttk.Button(side, text="✖ Remove selected", command=self.on_qual_remove).grid(
            row=6, column=0, sticky="we", pady=2)
        ttk.Button(side, text="Start / update qual track", command=self.on_qual_start).grid(
            row=7, column=0, sticky="we", pady=(8, 2))
        ttk.Label(side, text="One-pagers / step:").grid(row=8, column=0, sticky="w", pady=(6, 0))
        self.qual_batch = tk.IntVar(value=2)
        ttk.Spinbox(side, from_=1, to=4, textvariable=self.qual_batch, width=3).grid(
            row=9, column=0, sticky="w")

        # mode row — same pattern as tab 1
        ttk.Label(frm, text="Mode").grid(row=4, column=0, sticky="w", **pad)
        qmode_frame = ttk.Frame(frm)
        qmode_frame.grid(row=4, column=1, columnspan=3, sticky="w", **pad)
        self.qual_mode = tk.StringVar(value="prompt")
        ttk.Radiobutton(qmode_frame, text="Prompt — paste into ChatGPT (no key needed)",
                        value="prompt", variable=self.qual_mode,
                        command=self._apply_qual_mode).grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Radiobutton(qmode_frame, text="API — automatic ⚡ (keys in Settings)",
                        value="api", variable=self.qual_mode,
                        command=self._apply_qual_mode).grid(row=0, column=1, sticky="w")

        # two button rows — one per mode, toggled by _apply_qual_mode
        self.qual_prompt_btns = ttk.Frame(frm)
        self.qual_prompt_btns.grid(row=5, column=0, columnspan=4, sticky="w", **pad)
        ttk.Button(self.qual_prompt_btns, text="Copy prompt", command=self.on_qual_copy).grid(
            row=0, column=0, padx=4)
        ttk.Button(self.qual_prompt_btns, text="Next qual prompt ▶", command=self.on_qual_next).grid(
            row=0, column=1, padx=4)
        ttk.Button(self.qual_prompt_btns, text="Open report", command=self.on_qual_report).grid(
            row=0, column=2, padx=4)

        self.qual_api_btns = ttk.Frame(frm)
        self.qual_api_btns.grid(row=5, column=0, columnspan=4, sticky="w", **pad)
        self.qual_api_start_btn = ttk.Button(self.qual_api_btns, text="Start next step  ⚡",
                                             command=self.on_qual_api_step)
        self.qual_api_start_btn.grid(row=0, column=0, padx=4)
        ttk.Label(self.qual_api_btns, text="with:").grid(row=0, column=1, padx=(10, 2))
        self.qual_model_box = ttk.Combobox(self.qual_api_btns, textvariable=self.provider_label,
                                           state="readonly", width=30,
                                           postcommand=self._refresh_provider_labels)
        self.qual_model_box.grid(row=0, column=2, padx=2)
        self.qual_model_box.bind("<<ComboboxSelected>>", self._on_provider_picked)
        ttk.Button(self.qual_api_btns, text="Open report", command=self.on_qual_report).grid(
            row=0, column=3, padx=8)
        self.qual_agent_lbl = ttk.Label(frm, text="", foreground="#0a6", wraplength=820,
                                        justify="left")
        self.qual_agent_lbl.grid(row=6, column=0, columnspan=4, sticky="w", **pad)

        qp = ttk.Frame(frm)
        qp.grid(row=7, column=0, columnspan=4, sticky="we", **pad)
        self.qual_bar = ttk.Progressbar(qp, length=520, mode="determinate", maximum=100)
        self.qual_bar.grid(row=0, column=0, sticky="w")
        self.qual_eta_lbl = ttk.Label(qp, text="", foreground="#666")
        self.qual_eta_lbl.grid(row=0, column=1, sticky="w", padx=10)
        self.qual_lbl = ttk.Label(frm, text="—")
        self.qual_lbl.grid(row=8, column=0, columnspan=4, sticky="w", **pad)

        self.qual_txt = tk.Text(frm, height=13, width=96, wrap="word")
        self.qual_txt.grid(row=9, column=0, columnspan=4, sticky="nsew", **pad)
        self.qual_txt.configure(state="disabled")
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(9, weight=1)
        self.qual_rows: list[dict] = []
        self._refresh_provider_labels()   # fill the picker created above
        self._apply_qual_mode()

    def _apply_qual_mode(self):
        if self.qual_mode.get() == "api":
            self.qual_prompt_btns.grid_remove()
            self.qual_api_btns.grid()
        else:
            self.qual_api_btns.grid_remove()
            self.qual_prompt_btns.grid()
            self.qual_agent_lbl.configure(text="")

    def on_qual_api_step(self):
        if not self.run_dir:
            messagebox.showinfo("No run", "Load a run and start the qual track first.")
            return
        provider = self.provider.get()
        self.qual_api_start_btn.configure(state="disabled")
        self.qual_agent_lbl.configure(text=f"⚡ starting ({provider})…")

        def log(msg):
            self.root.after(0, lambda: (self.qual_agent_lbl.configure(text=msg),
                                        self.status.set(msg)))

        def work():
            try:
                from src import api_runner
                summary = api_runner.run_next_qual_step(self.run_dir, self.qual_batch.get(),
                                                        provider, log=log)
                kind, text = onepager.next_qual_prompt(self.run_dir, self.qual_batch.get())

                def done():
                    self._show_qual(text)
                    self.qual_agent_lbl.configure(text=f"✔ {summary}")
                    self.status.set(f"Qual API step done · next: {kind}")
                    self.qual_api_start_btn.configure(state="normal")
                    self._qual_refresh()
                self.root.after(0, done)
            except (SystemExit, Exception) as e:
                def fail():
                    self.qual_api_start_btn.configure(state="normal")
                    self.qual_agent_lbl.configure(text=f"✖ {e}")
                    messagebox.showwarning(
                        "Qual API step failed",
                        f"{e}\n\nCheck the API keys in Settings (or switch to Prompt mode).")
                self.root.after(0, fail)
        threading.Thread(target=work, daemon=True).start()

    def _qual_render_list(self):
        self.qual_lb.delete(0, "end")
        for r in self.qual_rows:
            mark = "✍ " if r.get("manual") else ""
            self.qual_lb.insert("end", f"{mark}{r['brand']}  ·  {r['segment'] or '—'}"
                                       f"  ·  angle: {r['angle']}")

    def _qual_manual_dialog(self, initial=None):
        """Modal form for a manual qual target: name + segment (+ notes)."""
        top = tk.Toplevel(self.root)
        top.title("Manual qual target")
        top.transient(self.root)
        top.grab_set()
        out = {}
        ttk.Label(top, text="Company name:").grid(row=0, column=0, sticky="w", padx=8, pady=4)
        name_v = tk.StringVar(value=(initial or {}).get("brand", ""))
        ttk.Entry(top, textvariable=name_v, width=38).grid(row=0, column=1, padx=8, pady=4)
        ttk.Label(top, text="Market segment:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        seg_v = tk.StringVar(value=(initial or {}).get("segment", ""))
        ttk.Entry(top, textvariable=seg_v, width=38).grid(row=1, column=1, padx=8, pady=4)
        ttk.Label(top, text="Notes (optional context\nyou know for sure):",
                  justify="left").grid(row=2, column=0, sticky="nw", padx=8, pady=4)
        notes_t = tk.Text(top, width=38, height=4, wrap="word")
        notes_t.grid(row=2, column=1, padx=8, pady=4)
        notes_t.insert("1.0", (initial or {}).get("notes", ""))
        ttk.Label(top, text="Only what you enter here counts as KNOWN — everything\n"
                            "else will be treated as unclear or hypothesis.",
                  foreground="#666", justify="left").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=8)

        def ok():
            if not name_v.get().strip() or not seg_v.get().strip():
                messagebox.showwarning("Missing", "Company name and segment are "
                                                  "required.", parent=top)
                return
            out.update(brand=name_v.get().strip(), segment=seg_v.get().strip(),
                       notes=notes_t.get("1.0", "end").strip())
            top.destroy()
        ttk.Button(top, text="Save", command=ok).grid(row=4, column=1, sticky="e",
                                                      padx=8, pady=8)
        top.wait_window()
        return out or None

    def on_qual_add_manual(self):
        vals = self._qual_manual_dialog()
        if not vals:
            return
        if any(runs._norm(r["brand"]) == runs._norm(vals["brand"]) for r in self.qual_rows):
            messagebox.showinfo("Duplicate", f"«{vals['brand']}» is already in the list.")
            return
        if not self.run_dir:
            # no past run selected — standalone qual-only container
            self.run_dir = onepager.create_manual_run()
            self.status.set(f"Created standalone qual run {self.run_dir.name} "
                            f"for manual targets.")
        self.qual_rows.append({"brand": vals["brand"], "segment": vals["segment"],
                               "angle": self.angle_var.get(),
                               "manual": {"segment": vals["segment"],
                                          "notes": vals["notes"]}})
        self._qual_render_list()

    def on_qual_edit_manual(self):
        sel = self.qual_lb.curselection()
        if len(sel) != 1:
            messagebox.showinfo("Edit", "Select exactly one row.")
            return
        row = self.qual_rows[sel[0]]
        if not row.get("manual"):
            messagebox.showinfo("Run-backed company",
                                "This company comes from the quantitative run — its "
                                "context is the verified record. Only the angle is "
                                "editable («Set angle → selected»).")
            return
        vals = self._qual_manual_dialog(initial={"brand": row["brand"],
                                                 "segment": row["manual"]["segment"],
                                                 "notes": row["manual"].get("notes", "")})
        if not vals:
            return
        if (runs._norm(vals["brand"]) != runs._norm(row["brand"]) and
                any(runs._norm(r["brand"]) == runs._norm(vals["brand"])
                    for r in self.qual_rows)):
            messagebox.showinfo("Duplicate", f"«{vals['brand']}» is already in the list.")
            return
        if vals["brand"] != row["brand"] and self.run_dir:
            onepager.remove_target(self.run_dir, row["brand"])   # renamed
        row.update(brand=vals["brand"], segment=vals["segment"],
                   manual={"segment": vals["segment"], "notes": vals["notes"]})
        self._qual_render_list()

    def on_qual_remove(self):
        sel = list(self.qual_lb.curselection())
        if not sel:
            messagebox.showinfo("Remove", "Select the row(s) to remove.")
            return
        for i in reversed(sel):
            row = self.qual_rows.pop(i)
            if self.run_dir:
                onepager.remove_target(self.run_dir, row["brand"])
        self._qual_render_list()

    def on_qual_load(self):
        if not self.run_dir:
            messagebox.showinfo("No run", "Load a run first (here via «Load past run…», "
                                          "or in tab 1).")
            return
        self.status.set(f"Loading gate-accepted companies from {self.run_dir.name}…")

        def work():
            try:
                g = runs.run_gate(self.run_dir, write_report=False)
                qmeta = onepager.load_meta(self.run_dir)
                from src import gate as _gate
                rows = []
                for e in g["accepted"]:
                    f = e["record"].get("fields") or {}
                    seg = str(_gate.value_of(f.get("segment")) or "")
                    angle = (qmeta["companies"].get(e["entity"], {}).get("angle")
                             or onepager.propose_angle(e["record"]))
                    rows.append({"brand": e["entity"], "segment": seg, "angle": angle})

                def done():
                    # run-backed rows first, then saved manual targets from
                    # qual_meta, then unsaved manual rows — deduped by name
                    # (a run-backed record always wins over a manual duplicate)
                    seen = {runs._norm(r["brand"]) for r in rows}
                    for brand, info in (qmeta.get("companies") or {}).items():
                        if (isinstance(info.get("manual"), dict)
                                and runs._norm(brand) not in seen):
                            rows.append({"brand": brand,
                                         "segment": info["manual"].get("segment", ""),
                                         "angle": info.get("angle", "competitor"),
                                         "manual": info["manual"]})
                            seen.add(runs._norm(brand))
                    for r in getattr(self, "qual_rows", []) or []:
                        if r.get("manual") and runs._norm(r["brand"]) not in seen:
                            rows.append(r)
                            seen.add(runs._norm(r["brand"]))
                    self.qual_rows = rows
                    self._qual_render_list()
                    goal = qmeta.get("research_goal", "")
                    if goal:
                        self.goal_txt.delete("1.0", "end")
                        self.goal_txt.insert("1.0", goal)
                    self.status.set(f"{self.run_dir.name}: {len(rows)} accepted companies — "
                                    f"confirm angles, enter the goal, then Start.")
                    self._qual_refresh()
                self.root.after(0, done)
            except Exception as e:
                self.root.after(0, lambda: messagebox.showwarning("Load failed", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def on_qual_set_angle(self):
        angle = self.angle_var.get()
        for i in self.qual_lb.curselection():
            self.qual_rows[i]["angle"] = angle
        self._qual_render_list()

    def on_qual_start(self):
        if not self.run_dir or not self.qual_rows:
            messagebox.showinfo("Nothing selected", "Load companies first.")
            return
        goal = self.goal_txt.get("1.0", "end").strip()
        try:
            onepager.setup(self.run_dir, goal,
                           {r["brand"]: r["angle"] for r in self.qual_rows},
                           manual={r["brand"]: r["manual"] for r in self.qual_rows
                                   if r.get("manual")})
            self.status.set(f"Qual track ready: {len(self.qual_rows)} companies — "
                            f"press Next qual prompt ▶")
            self._qual_refresh()
        except ValueError as e:
            messagebox.showwarning("Cannot start", str(e))

    def on_qual_next(self):
        if not self.run_dir:
            return
        self.status.set("Building qual prompt (gate + rendering)…")

        def work():
            try:
                kind, text = onepager.next_qual_prompt(self.run_dir, self.qual_batch.get())
                self.root.after(0, lambda: (self._show_qual(text),
                                            self.status.set(f"Qual step: {kind} — prompt ready"),
                                            self._qual_refresh()))
            except SystemExit as e:
                self.root.after(0, lambda: messagebox.showwarning("Qual", str(e)))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showwarning("Qual prompt failed", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def on_qual_copy(self):
        if not self.run_dir:
            return
        f = onepager.qual_dir(self.run_dir) / "prompt_qual.md"
        if f.exists():
            self.root.clipboard_clear()
            self.root.clipboard_append(f.read_text(encoding="utf-8"))
            self.status.set("Qual prompt copied to clipboard.")

    def on_qual_report(self):
        if not self.run_dir:
            return
        self.status.set("Preparing the final report (.docx)…")

        def work():
            try:
                if onepager.report_is_stale(self.run_dir):
                    onepager.build_report(self.run_dir)
                out = onepager.report_path(self.run_dir)
                self.root.after(0, lambda: (self.status.set(f"Report: {out.name}"),
                                            open_path(out), self._qual_refresh()))
            except SystemExit as e:
                self.root.after(0, lambda: messagebox.showwarning("Report", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def _qual_refresh(self):
        if not self.run_dir:
            return
        try:
            p = onepager.progress(self.run_dir)
            sel = max(p["selected"], 1)
            self.qual_bar["value"] = round(100 * p["accepted"] / sel)
            secs = p["pending"] * 240 + p["rejected"] * 90   # ~4 min research, ~1.5 min repair
            self.qual_eta_lbl.configure(text=runs.fmt_eta(secs or None))
            self.qual_lbl.configure(
                text=f"{p['accepted']}/{p['selected']} one-pagers accepted · "
                     f"{p['rejected']} rejected · {p['phase']}")
        except Exception:
            pass

    def _show_qual(self, text: str):
        self.qual_txt.configure(state="normal")
        self.qual_txt.delete("1.0", "end")
        self.qual_txt.insert("1.0", text)
        self.qual_txt.configure(state="disabled")

    # ══ tab 3 · settings ═════════════════════════════════════════════════════
    def _build_settings_tab(self, frm: ttk.Frame):
        pad = dict(padx=10, pady=4)
        ttk.Label(frm, text="API keys — optional; the Prompt mode works without them",
                  font=("", 12, "bold")).grid(row=0, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(frm, text="OpenAI API key:").grid(row=1, column=0, sticky="w", **pad)
        self.key_openai = tk.StringVar(value=os.environ.get("CHEAP_API_KEY", ""))
        ttk.Entry(frm, textvariable=self.key_openai, width=42, show="•").grid(
            row=1, column=1, sticky="w", **pad)
        ttk.Label(frm, text="Model:").grid(row=1, column=2, sticky="e", **pad)
        self.model_openai = tk.StringVar(value=os.environ.get("CHEAP_MODEL", "gpt-5.5"))
        ttk.Entry(frm, textvariable=self.model_openai, width=16).grid(row=1, column=3, sticky="w", **pad)

        ttk.Label(frm, text="Anthropic API key:").grid(row=2, column=0, sticky="w", **pad)
        self.key_anthropic = tk.StringVar(value=os.environ.get("ANTHROPIC_API_KEY", ""))
        ttk.Entry(frm, textvariable=self.key_anthropic, width=42, show="•").grid(
            row=2, column=1, sticky="w", **pad)
        ttk.Label(frm, text="Model:").grid(row=2, column=2, sticky="e", **pad)
        self.model_claude = tk.StringVar(value=os.environ.get("CLAUDE_MODEL", "claude-opus-4-8"))
        ttk.Entry(frm, textvariable=self.model_claude, width=16).grid(row=2, column=3, sticky="w", **pad)

        ttk.Label(frm, text="Grok (xAI) API key:").grid(row=3, column=0, sticky="w", **pad)
        self.key_grok = tk.StringVar(value=os.environ.get("GROK_API_KEY", ""))
        ttk.Entry(frm, textvariable=self.key_grok, width=42, show="•").grid(
            row=3, column=1, sticky="w", **pad)
        ttk.Label(frm, text="Model:").grid(row=3, column=2, sticky="e", **pad)
        self.model_grok = tk.StringVar(value=os.environ.get("GROK_MODEL", "grok-4.20-0309-reasoning"))
        ttk.Entry(frm, textvariable=self.model_grok, width=16).grid(row=3, column=3, sticky="w", **pad)

        ttk.Label(frm, text="DeepSeek API key:").grid(row=4, column=0, sticky="w", **pad)
        self.key_deepseek = tk.StringVar(value=os.environ.get("DEEPSEEK_API_KEY", ""))
        ttk.Entry(frm, textvariable=self.key_deepseek, width=42, show="•").grid(
            row=4, column=1, sticky="w", **pad)
        ttk.Label(frm, text="Model:").grid(row=4, column=2, sticky="e", **pad)
        self.model_deepseek = tk.StringVar(value=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
        ttk.Entry(frm, textvariable=self.model_deepseek, width=16).grid(row=4, column=3, sticky="w", **pad)

        ttk.Label(frm, text="Search API key:").grid(row=5, column=0, sticky="w", **pad)
        self.key_search = tk.StringVar(value=os.environ.get("SEARCH_API_KEY", ""))
        ttk.Entry(frm, textvariable=self.key_search, width=42, show="•").grid(
            row=5, column=1, sticky="w", **pad)
        ttk.Label(frm, text="(Brave)").grid(row=5, column=2, sticky="w", **pad)

        ttk.Label(frm, text="Default ⚡ provider:").grid(row=6, column=0, sticky="w", **pad)
        ttk.Combobox(frm, textvariable=self.provider, state="readonly",
                     values=["gpt", "claude", "grok", "deepseek"], width=10).grid(
            row=6, column=1, sticky="w", **pad)
        ttk.Button(frm, text="Save API keys", command=self.on_save_keys).grid(
            row=6, column=3, sticky="e", **pad)
        ttk.Label(frm, text="DeepSeek has no built-in web search — quantitative research on "
                            "DeepSeek runs on the app's own search+fetch tools and needs a "
                            "Search API key (free tier: api-dashboard.search.brave.com).",
                  foreground="#666").grid(row=7, column=0, columnspan=4, sticky="w", **pad)

        ttk.Separator(frm, orient="horizontal").grid(row=8, column=0, columnspan=4, sticky="we", pady=8)

        lay = ttk.Frame(frm)
        lay.grid(row=9, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(lay, text="Layout:", font=("", 12, "bold")).grid(row=0, column=0, padx=(0, 10))
        self.layout_mode = tk.StringVar(value="excel")
        ttk.Radiobutton(lay, text="Excel layout (quantitative research)", value="excel",
                        variable=self.layout_mode, command=self._apply_layout).grid(row=0, column=1, padx=6)
        ttk.Radiobutton(lay, text="Report layout (qualitative research)", value="onepager",
                        variable=self.layout_mode, command=self._apply_layout).grid(row=0, column=2, padx=6)

        # ── Excel layout editor ──────────────────────────────────────────────
        self.excel_frame = ttk.Frame(frm)
        self.excel_frame.grid(row=10, column=0, columnspan=4, sticky="nsew", **pad)
        cols = ("column", "origin", "description")
        self.schema_tree = ttk.Treeview(self.excel_frame, columns=cols, show="headings", height=11)
        for c, w in zip(cols, (200, 75, 480)):
            self.schema_tree.heading(c, text=c)
            self.schema_tree.column(c, width=w, anchor="w")
        self.schema_tree.grid(row=0, column=0, columnspan=5, sticky="nsew")
        sb = ttk.Scrollbar(self.excel_frame, orient="vertical", command=self.schema_tree.yview)
        self.schema_tree.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=5, sticky="ns")
        self.schema_tree.bind("<<TreeviewSelect>>", self._on_col_select)

        ef = ttk.Frame(self.excel_frame)
        ef.grid(row=1, column=0, columnspan=5, sticky="w", pady=6)
        ttk.Label(ef, text="Column:").grid(row=0, column=0, padx=2)
        self.col_name = tk.StringVar()
        ttk.Entry(ef, textvariable=self.col_name, width=22).grid(row=0, column=1, padx=2)
        ttk.Label(ef, text="what to research / format:").grid(row=0, column=2, padx=2)
        self.col_desc = tk.StringVar()
        ttk.Entry(ef, textvariable=self.col_desc, width=46).grid(row=0, column=3, padx=2)
        ttk.Button(ef, text="Add ▶", command=self.on_col_add).grid(row=0, column=4, padx=3)
        ttk.Button(ef, text="Save changes", command=self.on_col_save).grid(row=0, column=5, padx=3)
        ttk.Button(ef, text="Delete", command=self.on_col_delete).grid(row=0, column=6, padx=3)
        ttk.Button(ef, text="Default", command=self.on_col_reset).grid(row=0, column=7, padx=3)
        ttk.Label(self.excel_frame,
                  text="Changes apply to FUTURE runs. Custom columns: rename/edit/delete. "
                       "Core (schema/financial) columns: description editable, name fixed. "
                       "Derived columns are computed by the app.",
                  foreground="#666", wraplength=820, justify="left").grid(
            row=2, column=0, columnspan=5, sticky="w")

        # ── One-pager layout editor ──────────────────────────────────────────
        self.onepager_frame = ttk.Frame(frm)
        self.onepager_frame.grid(row=10, column=0, columnspan=4, sticky="nsew", **pad)
        bcols = ("block", "origin", "what to research")
        self.block_tree = ttk.Treeview(self.onepager_frame, columns=bcols, show="headings", height=11)
        for c, w in zip(bcols, (170, 75, 510)):
            self.block_tree.heading(c, text=c)
            self.block_tree.column(c, width=w, anchor="w")
        self.block_tree.grid(row=0, column=0, columnspan=5, sticky="nsew")
        bsb = ttk.Scrollbar(self.onepager_frame, orient="vertical", command=self.block_tree.yview)
        self.block_tree.configure(yscrollcommand=bsb.set)
        bsb.grid(row=0, column=5, sticky="ns")
        self.block_tree.bind("<<TreeviewSelect>>", self._on_block_select)

        bfm = ttk.Frame(self.onepager_frame)
        bfm.grid(row=1, column=0, columnspan=5, sticky="w", pady=6)
        ttk.Label(bfm, text="Block:").grid(row=0, column=0, padx=2)
        self.block_name = tk.StringVar()
        ttk.Entry(bfm, textvariable=self.block_name, width=22).grid(row=0, column=1, padx=2)
        ttk.Label(bfm, text="what to research:").grid(row=0, column=2, padx=2)
        self.block_desc = tk.StringVar()
        ttk.Entry(bfm, textvariable=self.block_desc, width=46).grid(row=0, column=3, padx=2)
        ttk.Button(bfm, text="Add ▶", command=self.on_block_add).grid(row=0, column=4, padx=3)
        ttk.Button(bfm, text="Save changes", command=self.on_block_save).grid(row=0, column=5, padx=3)
        ttk.Button(bfm, text="Delete", command=self.on_block_delete).grid(row=0, column=6, padx=3)
        ttk.Button(bfm, text="Default", command=self.on_block_reset).grid(row=0, column=7, padx=3)
        ttk.Label(self.onepager_frame,
                  text="Core blocks are the one-pager's validated structure (read-only). "
                       "Custom blocks are requested in every FUTURE qual prompt and rendered "
                       "after section 5 (and in the .docx report).",
                  foreground="#666", wraplength=820, justify="left").grid(
            row=2, column=0, columnspan=5, sticky="w")

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(10, weight=1)
        self._schema_refresh()
        self._blocks_refresh()
        self._apply_layout()

    def _apply_layout(self):
        if self.layout_mode.get() == "excel":
            self.onepager_frame.grid_remove()
            self.excel_frame.grid()
        else:
            self.excel_frame.grid_remove()
            self.onepager_frame.grid()

    # ── Excel layout handlers ─────────────────────────────────────────────────
    def _schema_refresh(self):
        from src import export_excel as xl
        self.schema_tree.delete(*self.schema_tree.get_children())
        desc = {f["name"]: f.get("desc", "") for f in runs.load_schema()["fields"]}
        derived = {
            "entity_type": "from entity_match (product/brand/company/group/foreign_entity)",
            "confidence_entity_match": "from entity_match.confidence",
            "financial_sources": "URLs behind registry/financial/headcount values",
            "other_sources": "URLs behind company/product/business values",
        }
        custom = set(xl.custom_columns())
        fin = set(xl.FINANCIAL_COLUMNS)
        for c in xl.get_columns():
            origin = ("custom" if c in custom else "derived" if c in derived
                      else "financial" if c in fin else "schema")
            self.schema_tree.insert("", "end", values=(c, origin,
                                                       (desc.get(c) or derived.get(c, ""))[:200]))

    def _selected_col(self):
        sel = self.schema_tree.selection()
        if not sel:
            return None
        v = self.schema_tree.item(sel[0], "values")
        return {"name": v[0], "origin": v[1]}

    def _on_col_select(self, _ev=None):
        c = self._selected_col()
        if not c:
            return
        self.col_name.set(c["name"])
        full = {f["name"]: f.get("desc", "") for f in runs.load_schema()["fields"]}
        self.col_desc.set(full.get(c["name"], ""))

    def on_col_add(self):
        try:
            runs.add_custom_field(self.col_name.get().strip(), self.col_desc.get())
            self._schema_refresh()
            self.status.set(f"Column '{self.col_name.get().strip()}' added for future runs.")
        except ValueError as e:
            messagebox.showwarning("Cannot add column", str(e))

    def on_col_save(self):
        c = self._selected_col()
        if not c:
            messagebox.showinfo("Select a column", "Click a row first, then edit and save.")
            return
        new_name, desc = self.col_name.get().strip(), self.col_desc.get().strip()
        try:
            if c["origin"] == "custom":
                runs.update_custom_field(c["name"], new_name, desc)
            elif c["origin"] in ("schema", "financial"):
                if new_name != c["name"]:
                    messagebox.showinfo("Name is fixed",
                                        "Core column names are used by the gate and the Excel "
                                        "grouping — only the description was updated.")
                runs.override_field_desc(c["name"], desc)
            else:
                messagebox.showinfo("Derived column",
                                    "This column is computed by the app and cannot be edited.")
                return
            self._schema_refresh()
            self.status.set(f"Column '{c['name']}' updated for future runs.")
        except ValueError as e:
            messagebox.showwarning("Cannot save", str(e))

    def on_col_delete(self):
        c = self._selected_col()
        if not c:
            return
        if c["origin"] != "custom":
            messagebox.showinfo("Core column", "Only custom columns can be deleted.")
            return
        try:
            runs.delete_custom_field(c["name"])
            self._schema_refresh()
            self.status.set(f"Column '{c['name']}' removed from future runs.")
        except ValueError as e:
            messagebox.showwarning("Cannot delete", str(e))

    def on_col_reset(self):
        if not messagebox.askyesno(
                "Reset Excel layout",
                "Remove ALL custom columns and description edits, returning the "
                "quantitative research layout to its defaults?\n\n"
                "Applies to future runs; existing run data is untouched."):
            return
        runs.reset_custom_fields()
        self._schema_refresh()
        self.status.set("Excel layout reset to defaults.")

    # ── Report layout handlers ────────────────────────────────────────────────
    def _blocks_refresh(self):
        self.block_tree.delete(*self.block_tree.get_children())
        for name, desc in onepager.BUILTIN_BLOCKS:
            self.block_tree.insert("", "end", values=(name, "core", desc[:200]))
        for b in onepager.custom_blocks():
            self.block_tree.insert("", "end", values=(b["name"], "custom", b.get("desc", "")[:200]))

    def _selected_block(self):
        sel = self.block_tree.selection()
        if not sel:
            return None
        v = self.block_tree.item(sel[0], "values")
        return {"name": v[0], "origin": v[1]}

    def _on_block_select(self, _ev=None):
        b = self._selected_block()
        if not b:
            return
        self.block_name.set(b["name"])
        descs = dict(onepager.BUILTIN_BLOCKS) | {x["name"]: x.get("desc", "")
                                                 for x in onepager.custom_blocks()}
        self.block_desc.set(descs.get(b["name"], ""))

    def on_block_add(self):
        try:
            onepager.add_block(self.block_name.get().strip(), self.block_desc.get())
            self._blocks_refresh()
            self.status.set(f"Block '{self.block_name.get().strip()}' added — it will be "
                            f"requested in future qual prompts and rendered in the report.")
        except ValueError as e:
            messagebox.showwarning("Cannot add block", str(e))

    def on_block_save(self):
        b = self._selected_block()
        if not b:
            messagebox.showinfo("Select a block", "Click a row first, then edit and save.")
            return
        if b["origin"] == "core":
            messagebox.showinfo("Core block", "Core blocks are the one-pager's validated "
                                              "structure — add a custom block instead.")
            return
        try:
            onepager.update_block(b["name"], self.block_name.get().strip(), self.block_desc.get())
            self._blocks_refresh()
            self.status.set(f"Block '{b['name']}' updated for future qual prompts.")
        except ValueError as e:
            messagebox.showwarning("Cannot save", str(e))

    def on_block_delete(self):
        b = self._selected_block()
        if not b:
            return
        if b["origin"] == "core":
            messagebox.showinfo("Core block", "Only custom blocks can be deleted.")
            return
        try:
            onepager.delete_block(b["name"])
            self._blocks_refresh()
            self.status.set(f"Block '{b['name']}' removed.")
        except ValueError as e:
            messagebox.showwarning("Cannot delete", str(e))

    def on_block_reset(self):
        if not messagebox.askyesno(
                "Reset Report layout",
                "Remove ALL custom blocks, returning the qualitative report "
                "layout to its default five sections?\n\n"
                "Applies to future qual prompts; existing one-pagers are untouched."):
            return
        onepager.reset_blocks()
        self._blocks_refresh()
        self.status.set("Report layout reset to defaults.")

    def on_save_keys(self):
        values = {
            "CHEAP_API_KEY": self.key_openai.get().strip(),
            "CHEAP_MODEL": self.model_openai.get().strip() or "gpt-5.5",
            "ANTHROPIC_API_KEY": self.key_anthropic.get().strip(),
            "CLAUDE_MODEL": self.model_claude.get().strip() or "claude-opus-4-8",
            "GROK_API_KEY": self.key_grok.get().strip(),
            "GROK_MODEL": self.model_grok.get().strip() or "grok-4.20-0309-reasoning",
            "DEEPSEEK_API_KEY": self.key_deepseek.get().strip(),
            "DEEPSEEK_MODEL": self.model_deepseek.get().strip() or "deepseek-chat",
            "SEARCH_API_KEY": self.key_search.get().strip(),
            "AGENT_MODE": self.provider.get(),
        }
        save_env({k: v for k, v in values.items() if v})
        try:
            from src import api_runner
            api_runner.apply_env(values)
        except Exception:
            pass
        self._refresh_provider_labels()   # tab-1 picker shows the new model names
        self.status.set("API keys saved and applied — ⚡ mode is ready.")

    # ── shared prompt display + polling ──────────────────────────────────────
    def _show_prompt(self, text: str):
        self.prompt_txt.configure(state="normal")
        self.prompt_txt.delete("1.0", "end")
        self.prompt_txt.insert("1.0", text)
        self.prompt_txt.configure(state="disabled")

    def _start_poll(self):
        if self._poll_id:
            self.root.after_cancel(self._poll_id)
        self._poll()

    def _poll(self):
        if self.run_dir:
            p = runs.progress(self.run_dir)
            self.bar["value"] = p["pct"]
            total = p["total"] if p["total"] is not None else "?"
            txt = f"{p['pct']}%  ·  {p['done']}/{total} accepted"
            if p.get("rejected"):
                txt += f"  ·  {p['rejected']} rejected"
            txt += f"  ·  {p['phase']}"
            self.progress_lbl.configure(text=txt)
            if self.mode.get() == "api":
                self.eta_lbl.configure(text=runs.fmt_eta(
                    runs.eta_seconds(self.run_dir, p.get("pending", 0), p.get("rejected", 0))))
            if p["done"] > 0:
                self.build_btn.configure(state="normal")
        self._poll_id = self.root.after(3000, self._poll)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
