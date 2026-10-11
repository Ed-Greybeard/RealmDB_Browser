"""Run on a machine with a display: python3 tests/ui_smoke.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk
import json
import sqlite3
import tempfile
from unittest.mock import patch
from browser import Browser


def main():
    root = tk.Tk()
    root.withdraw()
    try:
        app = Browser(root)
        app.open(Path(__file__).resolve().parents[1] / 'sample.realm')
        root.update()
        assert len(app.tables) == 2
        assert len(app.tree.get_children()) == 6
        with tempfile.TemporaryDirectory() as folder:
            with patch('browser.filedialog.askdirectory', return_value=folder), patch('browser.messagebox.showinfo') as info:
                app.export_database('csv')
                info.assert_called_once()
            assert (Path(folder) / 'sample_csv' / 'manifest.json').exists()
            output = Path(folder) / 'export.sqlite'
            with patch('browser.filedialog.asksaveasfilename', return_value=str(output)), patch('browser.messagebox.showinfo') as info:
                app.export_database('sqlite')
                info.assert_called_once()
            with sqlite3.connect(output) as connection:
                assert connection.execute('SELECT COUNT(*) FROM People').fetchone()[0] == 6
        app.sort_column('4')  # Numeric age, ascending (including -1).
        assert app.tree.get_children()[0] == '5'
        app.sort_column('4')
        assert app.tree.get_children()[0] == '1'
        assert app.tree.heading('4')['text'].endswith('▼')
        app.sort_column('1')
        assert app.tree.get_children()[0] == '0'
        app.sort_column('1')
        assert app.tree.get_children()[0] == '4'
        # Small pages exercise search navigation to sorted positions.
        app.PAGE_SIZE = 2
        app.pattern.insert(0, 'London')
        app.find()
        root.update()
        assert len(app.hits) >= 3
        assert app.mode.get() == 'Database'
        assert any(hit[0] == 2 for hit in app.hits)
        assert app.hit == 0
        assert app.tree.selection() == ('0',)
        assert app.page == app.row_order().index(0) // 2
        app.step(1)
        app.step(1)
        root.update()
        assert app.current == 1
        assert app.tree.selection() == ('1',)
        assert app.detail.tag_ranges('match')
        app.hit = len(app.hits) - 1
        app.step(1)
        assert app.hit == 0
        app.step(-1)
        assert app.hit == len(app.hits) - 1
        # Search from Nodes view still finds decoded records in another table.
        app.mode.set('Nodes')
        app.load_view()
        app.pattern.delete(0, 'end')
        app.pattern.insert(0, 'ABC-123')
        app.find()
        root.update()
        assert app.hits[0][:3] == (1, 2, 2)
        assert app.current == 1
        # Schema names are searchable even though they are not row values.
        app.pattern.delete(0, 'end')
        app.pattern.insert(0, 'email')
        app.find()
        root.update()
        assert app.hits
        assert all(hit[0] == 2 for hit in app.hits)
        app.pattern.delete(0, 'end')
        app.pattern.insert(0, '[')
        app.find()
        assert 'failed' in app.result.cget('text')
        app.mode.set('Nodes')
        app.load_view()
        assert len(app.tables[0].rows) == 24
        assert not app.hits
        # Recovery fixture was written and deleted by actual Realm Core.
        app.open(Path(__file__).resolve().parents[1] / 'sample_deleted.realm')
        app.recover_deleted()
        root.update()
        assert app.mode.get() == 'Recovery'
        assert app.recovered.rows
        assert all('recovered' in app.tree.item(i, 'tags') for i in app.tree.get_children())
        app.sort_column('4')
        app.pattern.delete(0, 'end')
        app.pattern.insert(0, 'Alice Morgan')
        app.find()
        root.update()
        assert app.hits
        assert all(app.tables[hit[0]] is app.recovered for hit in app.hits)
        assert 'recovered' in app.tree.item(app.tree.selection()[0], 'tags')
        assert app.detail.tag_ranges('match')
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'recovered.json'
            with patch('browser.filedialog.asksaveasfilename', return_value=str(output)):
                app.export_recovery()
            report = json.loads(output.read_text())
            assert len(report['candidates']) == len(app.recovered.rows)
            original = app.db.path.read_bytes()
            with patch('browser.filedialog.asksaveasfilename', return_value=str(app.db.path)), patch('browser.messagebox.showerror') as error:
                app.export_recovery()
                error.assert_called_once()
            assert app.db.path.read_bytes() == original
        print('UI smoke checks passed')
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
