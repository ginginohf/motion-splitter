"""Real Tk layout/theme check at a compact window size."""
import faulthandler
faulthandler.dump_traceback_later(30, exit=True)
import copy
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gui
from gui_components import descendants


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = gui.tk.Tk()
        def pump():
            root.after(80, root.quit)
            root.mainloop()
        try:
            with patch.object(gui, 'load_settings', return_value=copy.deepcopy(gui.DEFAULTS)), \
                 patch.object(gui, 'SETTINGS_FILE', Path(temp) / 'settings.json'):
                app = gui.App(root)
                root.geometry('900x640+40+40')
                pump()
                for dark in (True, False):
                    app.set_dark_theme(dark); app.update_widget_colors()
                    pump()
                    for tab, form in ((app.tab_split, app.split_form), (app.tab_enhance, app.enhance_form)):
                        app.notebook.select(tab); pump()
                        assert app.quick_start.winfo_ismapped()
                        assert app.quick_stop.winfo_rooty() + app.quick_stop.winfo_height() <= root.winfo_rooty()+root.winfo_height()
                        assert form.canvas.bbox('all')[2] >= form.canvas.winfo_width()
                        assert app.log_text.cget('bg') == app.entry_bg
                        assert app.log_enh.cget('bg') == app.entry_bg
                        assert app.preview_label.cget('bg') == 'black'
                        assert app.btn_stop.cget('style') == 'Danger.TButton'
                        assert gui.ttk.Style(root).lookup('TCombobox', 'fieldbackground', ('readonly',)) == app.entry_bg
                        fields = [w for w in descendants(form.content) if isinstance(w, gui.tk.Entry)]
                        field = fields[-1]
                        field.focus_force(); pump()
                        assert field.winfo_rooty() >= form.canvas.winfo_rooty()
                        assert field.winfo_rooty()+field.winfo_height() <= form.canvas.winfo_rooty()+form.canvas.winfo_height()
                        form.canvas.xview_moveto(0); form.canvas.yview_moveto(0); pump()
                app.show_help(); pump()
                help_window = [w for w in root.winfo_children() if isinstance(w, gui.tk.Toplevel)][0]
                help_window.destroy()
                faulthandler.cancel_dump_traceback_later()
                print('Tk layout: both tabs, both themes, 900x640, scrolling/focus, fixed actions and help passed.')
        finally:
            root.destroy()

if __name__ == '__main__':
    main()
