"""Simple pure Python, read-only Realm browser. Run: python3 browser.py [file.realm]"""
import argparse
import multiprocessing
import re
import json
import sqlite3
import math
from array import array
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from realm_reader import RealmFile, RealmError
from create_sample import create_sample
from search import search
from recovery import recover
from exporter import export_csv, export_sqlite


class Browser(ttk.Frame):
    PAGE_SIZE = 200

    def __init__(self, root):
        super().__init__(root, padding=8)
        self.pack(fill='both', expand=True)
        self.root = root
        self.db = None
        self.tables = []
        self.hits = []
        self.hit = -1
        self.page = 0
        self.current = 0
        self.sorts = {}
        self._orders = {}
        self._positions = {}
        self._grid_signature = None
        self.recovered = None
        self.recovery_notes = []
        root.title('Realm Browser')
        root.geometry('1050x650')
        toolbar = ttk.Frame(self)
        toolbar.pack(fill='x')
        ttk.Button(toolbar, text='Open Realm…', command=self.open_dialog).pack(side='left')
        ttk.Button(toolbar, text='Create test Realm…', command=self.sample).pack(side='left', padx=5)
        ttk.Button(toolbar, text='Recover deleted data', command=self.recover_deleted).pack(side='left', padx=5)
        ttk.Button(toolbar, text='Export recovery…', command=self.export_recovery).pack(side='left')
        self.mode = ttk.Combobox(toolbar, values=['Tables', 'Nodes', 'Database', 'Recovery'], state='readonly', width=10)
        self.mode.set('Tables')
        self.mode.pack(side='left')
        self.mode.bind('<<ComboboxSelected>>', lambda _: self.load_view())
        self.filename = ttk.Label(toolbar, text='Open a .realm file to begin')
        self.filename.pack(side='left', padx=8)
        exports = ttk.Frame(self)
        exports.pack(fill='x', pady=(5, 0))
        ttk.Button(exports, text='Export database to CSV…', command=lambda: self.export_database('csv')).pack(side='left')
        ttk.Button(exports, text='Export database to SQLite…', command=lambda: self.export_database('sqlite')).pack(side='left', padx=5)
        bar = ttk.Frame(self)
        bar.pack(fill='x', pady=8)
        ttk.Label(bar, text='Regex:').pack(side='left')
        self.pattern = ttk.Entry(bar)
        self.pattern.pack(side='left', fill='x', expand=True, padx=5)
        self.pattern.bind('<Return>', lambda _: self.find())
        self.ignore = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text='Ignore case', variable=self.ignore).pack(side='left')
        ttk.Button(bar, text='Search', command=self.find).pack(side='left', padx=5)
        self.previous = ttk.Button(bar, text='Previous result', command=lambda: self.step(-1), state='disabled')
        self.previous.pack(side='left')
        self.next = ttk.Button(bar, text='Next result', command=lambda: self.step(1), state='disabled')
        self.next.pack(side='left')
        self.result = ttk.Label(self, text='Regex search covers the entire database, including storage nodes.')
        self.result.pack(anchor='w')
        self.warning = ttk.Label(self, wraplength=1000, foreground='#955000')
        self.warning.pack(fill='x', pady=4)
        main = ttk.Panedwindow(self, orient='horizontal')
        main.pack(fill='both', expand=True)
        self.list = tk.Listbox(main, width=22, exportselection=False)
        self.list.bind('<<ListboxSelect>>', self.select_table)
        main.add(self.list, weight=1)
        grid = ttk.Frame(main)
        main.add(grid, weight=5)
        self.tree = ttk.Treeview(grid, show='headings', selectmode='browse')
        self.tree.tag_configure('recovered', background='#ffe0b2', foreground='#663c00')
        self.tree.grid(row=0, column=0, sticky='nsew')
        ys = ttk.Scrollbar(grid, orient='vertical', command=self.tree.yview)
        ys.grid(row=0, column=1, sticky='ns')
        xs = ttk.Scrollbar(grid, orient='horizontal', command=self.tree.xview)
        xs.grid(row=1, column=0, sticky='ew')
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        grid.rowconfigure(0, weight=1)
        grid.columnconfigure(0, weight=1)
        self.tree.bind('<<TreeviewSelect>>', self.details)
        pages = ttk.Frame(self)
        pages.pack(fill='x', pady=5)
        self.back = ttk.Button(pages, text='Previous page', command=lambda: self.turn_page(-1), state='disabled')
        self.back.pack(side='left')
        self.forward = ttk.Button(pages, text='Next page', command=lambda: self.turn_page(1), state='disabled')
        self.forward.pack(side='left', padx=5)
        self.status = ttk.Label(pages)
        self.status.pack(side='left')
        self.detail = tk.Text(self, height=7, wrap='word', state='disabled')
        self.detail.pack(fill='x')
        self.detail.tag_configure('match', background='#ffe082', foreground='black')
        root.bind('<F3>', lambda _: self.step(1))
        root.bind('<Shift-F3>', lambda _: self.step(-1))

    def open_dialog(self):
        path = filedialog.askopenfilename(filetypes=[('Realm databases', '*.realm'), ('All files', '*')])
        if path:
            self.open(path)

    def open(self, path):
        try:
            db = RealmFile(path)
            tables = db.tables()
        except (OSError, RealmError) as exc:
            messagebox.showerror('Cannot open Realm', str(exc))
            return
        self.db = db
        self.recovered = None
        self.recovery_notes = []
        self.sorts.clear()
        self._orders.clear()
        self._positions.clear()
        self.mode.set('Tables' if tables or not db.top else 'Nodes')
        self.filename.configure(text=f'{db.path.name} · format {db.version} · read only')
        self.load_view()

    def sample(self):
        path = filedialog.asksaveasfilename(defaultextension='.realm', initialfile='test.realm', filetypes=[('Realm database','*.realm')])
        if path:
            try:
                create_sample(path)
            except (OSError, ValueError) as exc:
                messagebox.showerror('Cannot create sample', str(exc))
                return
            self.open(path)

    def reset_search(self):
        self.hits, self.hit = [], -1
        self.previous.configure(state='disabled')
        self.next.configure(state='disabled')
        self.result.configure(text='Regex search covers the entire database, including storage nodes.')

    def recover_deleted(self):
        if not self.db:
            return
        try:
            recovered, notes = recover(self.db)
        except (RealmError, OSError) as exc:
            messagebox.showerror('Recovery failed', str(exc))
            return
        if self.recovered is not None:
            self._orders.pop(id(self.recovered), None)
            self._positions.pop(id(self.recovered), None)
        self.recovered, self.recovery_notes = recovered, notes
        self.mode.set('Recovery')
        self.load_view()

    def export_recovery(self):
        if self.recovered is None:
            self.recover_deleted()
        if self.recovered is None:
            return
        path = filedialog.asksaveasfilename(defaultextension='.json', initialfile='recovery.json', filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            # Never overwrite the source Realm, including through a symlink.
            if Path(path).resolve() == self.db.path.resolve():
                raise ValueError('Choose a different file from the source database.')
            report = {'source': str(self.db.path), 'notes': self.recovery_notes,
                      'candidates': [dict(zip(self.recovered.columns, row)) for row in self.recovered.rows]}
            with Path(path).open('x', encoding='utf-8') as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
        except (OSError, ValueError) as exc:
            messagebox.showerror('Cannot export recovery', str(exc))

    def database_tables(self):
        tables = self.db.tables() + [self.db.nodes()]
        if self.recovered is not None:
            tables.append(self.recovered)
        return tables

    def export_database(self, format):
        if not self.db:
            return
        if format == 'csv':
            parent = filedialog.askdirectory(title='Choose a parent folder for the CSV export')
            if not parent:
                return
            path = Path(parent) / (self.db.path.stem + '_csv')
            operation = export_csv
        else:
            path = filedialog.asksaveasfilename(title='Export database to SQLite',
                    defaultextension='.sqlite', initialfile=self.db.path.stem + '.sqlite',
                    filetypes=[('SQLite database', '*.sqlite'), ('All files', '*')])
            if not path:
                return
            operation = export_sqlite
        try:
            warnings = operation(self.db, path)
        except (OSError, ValueError, sqlite3.Error) as exc:
            messagebox.showerror('Export failed', str(exc))
            return
        message = f'Exported live decoded tables to {path}.'
        if warnings:
            messagebox.showwarning('Partial export', message + '\n\nSkipped data:\n' + '\n'.join(warnings))
        else:
            messagebox.showinfo('Export complete', message)

    def load_view(self, tables=None):
        self.reset_search()
        self.tables = []
        if self.db:
            try:
                if tables is not None:
                    self.tables = tables
                elif self.mode.get() == 'Tables':
                    self.tables = self.db.tables()
                elif self.mode.get() == 'Nodes':
                    self.tables = [self.db.nodes()]
                elif self.mode.get() == 'Recovery':
                    self.tables = [self.recovered] if self.recovered is not None else []
                else:
                    self.tables = self.database_tables()
                self.warning.configure(text=' '.join(self.db.warnings) + (' Nodes show storage arrays, not decoded records. Values may appear in both tables and nodes.' if self.mode.get() != 'Tables' else ''))
                if self.mode.get() in ('Recovery', 'Database') and self.recovered is not None:
                    self.warning.configure(text=' '.join(self.recovery_notes))
            except RealmError as exc:
                self.warning.configure(text=str(exc))
        self.list.delete(0, 'end')
        for table in self.tables:
            self.list.insert('end', f'{table.name} ({len(table.rows)})')
        self.current, self.page = 0, 0
        if self.tables:
            self.list.selection_set(0)
        self.render()

    def select_table(self, _=None):
        selection = self.list.curselection()
        if selection:
            self.current, self.page = selection[0], 0
            self.render()

    def row_order(self):
        table = self.tables[self.current]
        column, descending = self.sorts.get(table.name, ('row', False))
        if column == 'row':
            return range(len(table.rows)-1, -1, -1) if descending else range(len(table.rows))
        cache_key = (id(table), column, descending, len(table.rows))
        cached = self._orders.get(id(table))
        if cached is not None and cached[1] == cache_key:
            return cached[2]
        ci = int(column)

        def key(ri):
            value = table.rows[ri][ci]
            if value is None:
                return (0, '')
            if isinstance(value, (int, float)):
                if isinstance(value, float) and math.isnan(value):
                    return (1, 0, 0)
                return (1, 1, value)
            return (2, str(value).casefold())

        order = sorted(range(len(table.rows)), key=key, reverse=descending)
        self._orders[id(table)] = (table, cache_key, order)
        self._positions.pop(id(table), None)
        return order

    def row_position(self, ri):
        table = self.tables[self.current]
        order = self.row_order()
        if isinstance(order, range):
            return order.index(ri)
        positions = self._positions.get(id(table))
        if positions is None:
            positions = array('I', [0]) * len(order)
            for position, original in enumerate(order):
                positions[original] = position
            self._positions[id(table)] = positions
        return positions[ri]

    def sort_column(self, column):
        if not self.tables:
            return
        table = self.tables[self.current]
        previous, descending = self.sorts.get(table.name, (None, False))
        self.sorts[table.name] = (column, not descending if previous == column else False)
        self.page = 0
        self.render()

    def render(self):
        self.tree.delete(*self.tree.get_children())
        self.set_detail('')
        self.back.configure(state='disabled')
        self.forward.configure(state='disabled')
        if not self.tables:
            self.tree.configure(columns=())
            self._grid_signature = None
            self.status.configure(text='No decoded tables. Try Nodes view for unsupported layouts.')
            return
        table = self.tables[self.current]
        columns = ['row'] + [str(i) for i in range(len(table.columns))]
        sort_column, descending = self.sorts.get(table.name, (None, False))
        signature = (id(table), sort_column, descending)
        if signature != self._grid_signature:
            self.tree.configure(columns=columns)
            for key, name in zip(columns, ['Row'] + table.columns):
                indicator = (' ▼' if descending else ' ▲') if key == sort_column else ''
                self.tree.heading(key, text=name + indicator, command=lambda column=key: self.sort_column(column))
                if self._grid_signature is None or self._grid_signature[0] != id(table):
                    self.tree.column(key, width=70 if key == 'row' else 180, stretch=False)
            self._grid_signature = signature
        start = self.page * self.PAGE_SIZE
        for ri in self.row_order()[start:start+self.PAGE_SIZE]:
            row = table.rows[ri]
            tags = ('recovered',) if table is self.recovered else ()
            self.tree.insert('', 'end', iid=str(ri), values=[ri+1] + [str(v)[:500] for v in row], tags=tags)
        self.status.configure(text=f'{table.name} · rows {start+1 if table.rows else 0}–{min(start+self.PAGE_SIZE,len(table.rows))} of {len(table.rows)}')
        self.back.configure(state='normal' if self.page else 'disabled')
        self.forward.configure(state='normal' if start+self.PAGE_SIZE < len(table.rows) else 'disabled')

    def turn_page(self, delta):
        if self.tables:
            pages = max(1, (len(self.tables[self.current].rows)+self.PAGE_SIZE-1)//self.PAGE_SIZE)
            self.page = max(0, min(pages-1, self.page+delta))
            self.render()

    def set_detail(self, text, span=None):
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('1.0', text)
        if span:
            self.detail.tag_add('match', f'1.0 + {span[0]} chars', f'1.0 + {span[1]} chars')
            self.detail.see(f'1.0 + {span[0]} chars')
        self.detail.configure(state='disabled')

    def details(self, _=None):
        selection = self.tree.selection()
        if selection and self.tables:
            row = self.tables[self.current].rows[int(selection[0])]
            self.set_detail('\n'.join(f'{name}: {value}' for name, value in zip(self.tables[self.current].columns, row)))

    def find(self):
        self.reset_search()
        if not self.db:
            return
        if not self.pattern.get():
            self.result.configure(text='Enter a regular expression first.')
            return
        try:
            # Build the scope independently of the current table, page or view.
            # Reachable nodes also cover schemas and tables we cannot decode.
            tables = self.database_tables()
            hits, capped = search(tables, self.pattern.get(), self.ignore.get())
            self.mode.set('Database')
            self.load_view(tables=tables)
            self.hits = hits
        except (ValueError, re.error, TimeoutError, EOFError, OSError) as exc:
            self.result.configure(text=f'Search failed: {exc}')
            return
        if not self.hits:
            self.result.configure(text='No matches.')
            return
        self.cap = ' (limited to 10,000 matches)' if capped else ''
        self.previous.configure(state='normal')
        self.next.configure(state='normal')
        self.step(1)

    def step(self, delta):
        if not self.hits:
            return
        self.hit = (self.hit + delta) % len(self.hits)
        ti, ri, ci, begin, end = self.hits[self.hit]
        self.current = ti
        self.page = self.row_position(ri) // self.PAGE_SIZE
        self.list.selection_clear(0, 'end')
        self.list.selection_set(ti)
        self.list.see(ti)
        self.render()
        self.tree.selection_set(str(ri))
        self.tree.see(str(ri))
        # Selection events run after this callback; show matched cell after them.
        table = self.tables[ti]
        self.root.after_idle(lambda: self.set_detail(str(table.rows[ri][ci]), (begin, end)))
        self.result.configure(text=f'Match {self.hit+1} of {len(self.hits)}{self.cap} · {table.name} · row {ri+1} · {table.columns[ci]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?')
    args = parser.parse_args()
    root = tk.Tk()
    browser = Browser(root)
    if args.path:
        browser.open(args.path)
    root.mainloop()


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
