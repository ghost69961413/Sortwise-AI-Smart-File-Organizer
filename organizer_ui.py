#!/usr/bin/env python3
"""Phase 7: Simple desktop UI for the AI file organizer."""

from __future__ import annotations

import subprocess
import queue
import sys
import threading
from collections import Counter
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, Y, filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from file_organizer import (
    OrganizationDecision,
    apply_manual_override,
    organize_directory,
    preview_manual_override,
)


COMMON_OVERRIDE_FOLDERS = [
    "Others/ManualReview",
    "Others/NeedsReview",
    "Images/Personal",
    "Images/Nature",
    "Images/Events",
    "PDFs/Notes",
    "PDFs/Bills",
    "PDFs/Reports",
    "Documents/Resume",
    "Documents/Assignments",
    "Documents/Letters",
    "Documents/General",
    "Videos/Meetings",
    "Videos/ScreenRecordings",
    "Videos/Tutorials",
    "Videos/Events",
    "Videos/Travel",
    "Videos/SocialClips",
    "Videos/General",
]


class OrganizerApp(tk.Tk):
    """Tkinter UI to select folders, run organization, and view results."""

    def __init__(self) -> None:
        super().__init__()
        self.title("AI File Organizer - Final System")
        self.geometry("1120x700")
        self.minsize(960, 560)

        self.source_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.move_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.recursive_var = tk.BooleanVar(value=True)
        self.override_folder_var = tk.StringVar(value="Others/ManualReview")
        self.status_var = tk.StringVar(value="Select folders and click Start Organizing.")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_text_var = tk.StringVar(value="0%")
        self._last_run_was_dry_run = False

        self._all_decisions: list[OrganizationDecision] = []
        self._row_decisions: dict[str, OrganizationDecision] = {}
        self._worker_events: queue.Queue[tuple] = queue.Queue()

        self._build_layout()
        self.after(100, self._poll_worker_events)

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=BOTH, expand=True)

        self._build_controls(root)
        self._build_results(root)

    def _build_controls(self, parent: ttk.Frame) -> None:
        controls = ttk.LabelFrame(parent, text="Organizer Controls", padding=10)
        controls.pack(fill=X, padx=2, pady=(0, 10))

        source_label = ttk.Label(controls, text="Source Folder")
        source_label.grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        source_entry = ttk.Entry(controls, textvariable=self.source_var)
        source_entry.grid(row=0, column=1, sticky="ew", pady=4)
        source_button = ttk.Button(
            controls, text="Browse...", command=self._choose_source_folder
        )
        source_button.grid(row=0, column=2, padx=(8, 0), pady=4)

        destination_label = ttk.Label(controls, text="Destination Folder")
        destination_label.grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        destination_entry = ttk.Entry(controls, textvariable=self.destination_var)
        destination_entry.grid(row=1, column=1, sticky="ew", pady=4)
        destination_button = ttk.Button(
            controls, text="Browse...", command=self._choose_destination_folder
        )
        destination_button.grid(row=1, column=2, padx=(8, 0), pady=4)

        options_frame = ttk.Frame(controls)
        options_frame.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 2))

        move_checkbox = ttk.Checkbutton(
            options_frame, text="Move Files (instead of copy)", variable=self.move_var
        )
        move_checkbox.pack(side=LEFT, padx=(0, 12))
        dry_run_checkbox = ttk.Checkbutton(
            options_frame, text="Dry Run (preview only)", variable=self.dry_run_var
        )
        dry_run_checkbox.pack(side=LEFT, padx=(0, 12))
        recursive_checkbox = ttk.Checkbutton(
            options_frame, text="Include Subfolders", variable=self.recursive_var
        )
        recursive_checkbox.pack(side=LEFT)

        button_bar = ttk.Frame(controls)
        button_bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.start_button = ttk.Button(
            button_bar, text="Start Organizing", command=self._start_organizing
        )
        self.start_button.pack(side=LEFT)

        open_button = ttk.Button(
            button_bar, text="Open Destination", command=self._open_destination_folder
        )
        open_button.pack(side=LEFT, padx=(8, 0))

        status_label = ttk.Label(button_bar, textvariable=self.status_var)
        status_label.pack(side=LEFT, padx=(16, 0))

        override_bar = ttk.Frame(controls)
        override_bar.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        override_label = ttk.Label(override_bar, text="Manual Override Folder")
        override_label.pack(side=LEFT)

        self.override_combo = ttk.Combobox(
            override_bar,
            textvariable=self.override_folder_var,
            values=COMMON_OVERRIDE_FOLDERS,
            width=32,
        )
        self.override_combo.pack(side=LEFT, padx=(8, 8))

        self.preview_override_button = ttk.Button(
            override_bar, text="Preview Override", command=self._preview_override_selected
        )
        self.preview_override_button.pack(side=LEFT, padx=(0, 8))

        self.apply_override_button = ttk.Button(
            override_bar, text="Apply Override", command=self._apply_override_selected
        )
        self.apply_override_button.pack(side=LEFT)

        progress_bar = ttk.Frame(controls)
        progress_bar.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Progressbar(
            progress_bar, variable=self.progress_var, maximum=100, mode="determinate"
        ).pack(side=LEFT, fill=X, expand=True)
        ttk.Label(progress_bar, textvariable=self.progress_text_var, width=13, anchor="e").pack(
            side=LEFT, padx=(10, 0)
        )

        controls.columnconfigure(1, weight=1)

    def _build_results(self, parent: ttk.Frame) -> None:
        results = ttk.LabelFrame(parent, text="Organized Results", padding=10)
        results.pack(fill=BOTH, expand=True, padx=2, pady=(0, 2))

        self.summary_text = tk.Text(results, height=6, wrap="word")
        self.summary_text.insert(
            END,
            "Summary will appear here after organizing files.\n",
        )
        self.summary_text.configure(state="disabled")
        self.summary_text.pack(fill=X, pady=(0, 8))

        table_frame = ttk.Frame(results)
        table_frame.pack(fill=BOTH, expand=True)

        columns = (
            "action",
            "review",
            "base",
            "content",
            "folder",
            "source",
            "destination",
            "confidence",
        )
        self.results_table = ttk.Treeview(table_frame, columns=columns, show="headings", height=16)

        self.results_table.heading("action", text="Action")
        self.results_table.heading("review", text="Review")
        self.results_table.heading("base", text="Base Type")
        self.results_table.heading("content", text="Content Category")
        self.results_table.heading("folder", text="Folder")
        self.results_table.heading("source", text="Source File")
        self.results_table.heading("destination", text="Destination File")
        self.results_table.heading("confidence", text="Confidence")

        self.results_table.column("action", width=90, anchor="w")
        self.results_table.column("review", width=80, anchor="center")
        self.results_table.column("base", width=90, anchor="w")
        self.results_table.column("content", width=170, anchor="w")
        self.results_table.column("folder", width=180, anchor="w")
        self.results_table.column("source", width=180, anchor="w")
        self.results_table.column("destination", width=220, anchor="w")
        self.results_table.column("confidence", width=90, anchor="center")

        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.results_table.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.results_table.xview)
        self.results_table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.results_table.tag_configure("needs_review", background="#fff4d6")

        self.results_table.pack(side=LEFT, fill=BOTH, expand=True)
        y_scroll.pack(side=RIGHT, fill=Y)
        x_scroll.pack(side="bottom", fill=X)

        self.results_table.bind("<<TreeviewSelect>>", self._on_table_select)

        self.detail_text = tk.Text(results, height=5, wrap="word")
        self.detail_text.insert(END, "Select a row to view reasoning details.\n")
        self.detail_text.configure(state="disabled")
        self.detail_text.pack(fill=X, pady=(8, 0))

    def _choose_source_folder(self) -> None:
        directory = filedialog.askdirectory(title="Choose Source Folder")
        if not directory:
            return
        self.source_var.set(directory)

        if not self.destination_var.get().strip():
            default_destination = Path(directory).expanduser().resolve() / "organized_output"
            self.destination_var.set(str(default_destination))

    def _choose_destination_folder(self) -> None:
        directory = filedialog.askdirectory(title="Choose Destination Folder")
        if directory:
            self.destination_var.set(directory)

    def _start_organizing(self) -> None:
        source_value = self.source_var.get().strip()
        destination_value = self.destination_var.get().strip()

        if not source_value:
            messagebox.showerror("Missing Source", "Please choose a source folder.")
            return
        if not destination_value:
            messagebox.showerror("Missing Destination", "Please choose a destination folder.")
            return

        source = Path(source_value).expanduser().resolve()
        destination = Path(destination_value).expanduser().resolve()

        if not source.exists() or not source.is_dir():
            messagebox.showerror("Invalid Source", f"Source folder is invalid:\n{source}")
            return

        self._set_busy(True)
        self.progress_var.set(0)
        self.progress_text_var.set("0% (0 files)")
        self.status_var.set("Organizing files... this may take a moment.")
        self._clear_results()

        worker = threading.Thread(
            target=self._run_organizer_worker,
            args=(source, destination, self.recursive_var.get(), self.move_var.get(), self.dry_run_var.get()),
            daemon=True,
        )
        worker.start()

    def _run_organizer_worker(
        self,
        source: Path,
        destination: Path,
        recursive: bool,
        move_files: bool,
        dry_run: bool,
    ) -> None:
        try:
            decisions = organize_directory(
                source_directory=source,
                destination_root=destination,
                recursive=recursive,
                move_files=move_files,
                dry_run=dry_run,
                progress_callback=lambda completed, total: self._worker_events.put(
                    ("progress", completed, total)
                ),
            )
            self._worker_events.put(("success", decisions, destination, dry_run))
        except Exception as error:  # pragma: no cover - GUI path
            self._worker_events.put(("error", str(error)))

    def _poll_worker_events(self) -> None:
        """Apply background-worker updates on Tk's main thread."""
        while True:
            try:
                event = self._worker_events.get_nowait()
            except queue.Empty:
                break

            if event[0] == "progress":
                self._update_progress(event[1], event[2])
            elif event[0] == "success":
                self._on_organize_success(event[1], event[2], event[3])
            elif event[0] == "error":
                self._on_organize_error(event[1])

        self.after(100, self._poll_worker_events)

    def _update_progress(self, completed: int, total: int) -> None:
        percent = round(completed / total * 100) if total else 0
        self.progress_var.set(percent)
        self.progress_text_var.set(f"{percent}% ({completed}/{total} files)")

    def _on_organize_success(
        self, decisions: list[OrganizationDecision], destination: Path, dry_run: bool
    ) -> None:
        self._last_run_was_dry_run = dry_run
        self._all_decisions = decisions
        self._populate_table(decisions)
        self._render_summary(decisions, destination, dry_run)

        if not decisions:
            self.status_var.set("No files found to organize.")
            self.progress_var.set(100)
            self.progress_text_var.set("100% (0 files)")
        else:
            self.status_var.set(f"Done. Processed {len(decisions)} file(s).")
        self._set_busy(False)

    def _on_organize_error(self, error_message: str) -> None:
        self._set_busy(False)
        self.status_var.set("Organization failed.")
        messagebox.showerror("Organizer Error", error_message)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.start_button.configure(state=state)
        self.preview_override_button.configure(state=state)
        self.apply_override_button.configure(state=state)
        self.override_combo.configure(state=state)

    def _clear_results(self) -> None:
        self._all_decisions = []
        self._row_decisions.clear()

        for row in self.results_table.get_children():
            self.results_table.delete(row)

        self._set_text(self.summary_text, "Running...\n")
        self._set_text(self.detail_text, "Select a row to view reasoning details.\n")

    def _populate_table(self, decisions: list[OrganizationDecision]) -> None:
        self._row_decisions.clear()
        for row in self.results_table.get_children():
            self.results_table.delete(row)

        for decision in decisions:
            self._insert_decision_row(decision)

    def _insert_decision_row(self, decision: OrganizationDecision) -> None:
        source_name = Path(decision.source_path).name
        destination_name = Path(decision.destination_path).name
        review_value = "Yes" if decision.needs_manual_review else ""
        row_tags = ("needs_review",) if decision.needs_manual_review else ()

        row_id = self.results_table.insert(
            "",
            END,
            values=(
                decision.action,
                review_value,
                decision.base_category,
                decision.content_category,
                decision.folder_path,
                source_name,
                destination_name,
                f"{decision.confidence:.2f}",
            ),
            tags=row_tags,
        )
        self._row_decisions[str(row_id)] = decision

    def _replace_decision_row(self, row_id: str, decision: OrganizationDecision) -> None:
        source_name = Path(decision.source_path).name
        destination_name = Path(decision.destination_path).name
        review_value = "Yes" if decision.needs_manual_review else ""
        row_tags = ("needs_review",) if decision.needs_manual_review else ()

        self.results_table.item(
            row_id,
            values=(
                decision.action,
                review_value,
                decision.base_category,
                decision.content_category,
                decision.folder_path,
                source_name,
                destination_name,
                f"{decision.confidence:.2f}",
            ),
            tags=row_tags,
        )
        self._row_decisions[row_id] = decision
        self._all_decisions = self._decisions_in_table_order()

    def _decisions_in_table_order(self) -> list[OrganizationDecision]:
        ordered: list[OrganizationDecision] = []
        for row_id in self.results_table.get_children():
            decision = self._row_decisions.get(str(row_id))
            if decision is not None:
                ordered.append(decision)
        return ordered

    def _selected_row_decision(self) -> tuple[str, OrganizationDecision] | None:
        selection = self.results_table.selection()
        if not selection:
            return None
        row_id = str(selection[0])
        decision = self._row_decisions.get(row_id)
        if decision is None:
            return None
        return row_id, decision

    def _preview_override_selected(self) -> None:
        selected = self._selected_row_decision()
        if selected is None:
            messagebox.showinfo("Manual Override", "Select a result row first.")
            return

        destination_value = self.destination_var.get().strip()
        if not destination_value:
            messagebox.showerror("Missing Destination", "Choose a destination folder first.")
            return

        manual_folder = self.override_folder_var.get().strip()
        row_id, decision = selected
        try:
            updated = preview_manual_override(
                decision=decision,
                destination_root=destination_value,
                manual_folder=manual_folder,
            )
        except Exception as error:
            messagebox.showerror("Manual Override", str(error))
            return

        self._replace_decision_row(row_id, updated)
        destination = Path(destination_value).expanduser().resolve()
        self._render_summary(self._all_decisions, destination, self._last_run_was_dry_run)
        self.status_var.set("Manual override preview updated for selected file.")
        self._on_table_select(None)

    def _apply_override_selected(self) -> None:
        selected = self._selected_row_decision()
        if selected is None:
            messagebox.showinfo("Manual Override", "Select a result row first.")
            return

        destination_value = self.destination_var.get().strip()
        if not destination_value:
            messagebox.showerror("Missing Destination", "Choose a destination folder first.")
            return

        if self._last_run_was_dry_run:
            messagebox.showinfo(
                "Dry Run Active",
                "Current run is a dry run. Override is preview-only.\n"
                "Disable Dry Run and run again to apply filesystem changes.",
            )
            self._preview_override_selected()
            return

        manual_folder = self.override_folder_var.get().strip()
        row_id, decision = selected
        try:
            updated = apply_manual_override(
                decision=decision,
                destination_root=destination_value,
                manual_folder=manual_folder,
                dry_run=False,
            )
        except Exception as error:
            messagebox.showerror("Manual Override", str(error))
            return

        self._replace_decision_row(row_id, updated)
        destination = Path(destination_value).expanduser().resolve()
        self._render_summary(self._all_decisions, destination, self._last_run_was_dry_run)
        self.status_var.set("Manual override applied to selected file.")
        self._on_table_select(None)

    def _render_summary(
        self,
        decisions: list[OrganizationDecision],
        destination: Path,
        dry_run: bool,
    ) -> None:
        if not decisions:
            self._set_text(
                self.summary_text,
                "No files were processed.\nCheck the selected folder or recursive setting.\n",
            )
            return

        action_counts = Counter(decision.action for decision in decisions)
        folder_counts = Counter(decision.folder_path for decision in decisions)
        review_count = sum(1 for decision in decisions if decision.needs_manual_review)

        lines: list[str] = []
        lines.append(f"Destination: {destination}")
        lines.append(f"Total files processed: {len(decisions)}")
        lines.append(f"Mode: {'Dry Run (no writes)' if dry_run else 'Live Run'}")
        lines.append(f"Needs manual review: {review_count}")
        lines.append("")
        lines.append("Actions:")
        for action, count in sorted(action_counts.items()):
            lines.append(f"- {action}: {count}")
        lines.append("")
        lines.append("Top destination folders:")
        for folder, count in folder_counts.most_common(10):
            lines.append(f"- {folder}: {count}")
        lines.append("")
        lines.append("Tip: rows marked 'Review=Yes' were routed to Others for verification.")
        lines.append("Tip: select a row and use Manual Override to correct folder placement.")

        self._set_text(self.summary_text, "\n".join(lines) + "\n")

    def _on_table_select(self, _event: tk.Event) -> None:
        selection = self.results_table.selection()
        if not selection:
            return

        decision = self._row_decisions.get(selection[0])
        if decision is None:
            return

        tags = ", ".join(decision.tags) if decision.tags else "none"
        lines = [
            f"Source: {decision.source_path}",
            f"Destination: {decision.destination_path}",
            f"Action: {decision.action}",
            f"Folder: {decision.folder_path}",
            f"Base Type: {decision.base_category}",
            f"Content Category: {decision.content_category}",
            f"Confidence: {decision.confidence:.2f}",
            f"Tags: {tags}",
            f"Reason: {decision.reason}",
        ]
        if decision.needs_manual_review:
            lines.append(f"Review Required: Yes ({decision.review_reason})")
        else:
            lines.append("Review Required: No")
        self._set_text(self.detail_text, "\n".join(lines) + "\n")

    def _open_destination_folder(self) -> None:
        destination_value = self.destination_var.get().strip()
        if not destination_value:
            messagebox.showinfo("Open Destination", "Choose a destination folder first.")
            return

        destination = Path(destination_value).expanduser().resolve()
        if not destination.exists():
            messagebox.showinfo("Open Destination", f"Folder does not exist yet:\n{destination}")
            return

        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(destination)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(destination)])
            else:
                subprocess.Popen(["xdg-open", str(destination)])
        except Exception as error:  # pragma: no cover - GUI path
            messagebox.showerror("Open Destination Failed", str(error))

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")


def main() -> int:
    app = OrganizerApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
