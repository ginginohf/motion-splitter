"""Scrollable forms and consistent Tk/ttk colors."""
import tkinter as tk
from tkinter import ttk


def ActionButton(parent, **options):
    # ttk renders button colors consistently where Aqua ignores Tk backgrounds.
    for key in ('bg', 'fg', 'background', 'foreground'):
        options.pop(key, None)
    return ttk.Button(parent, **options)


class ScrollForm:
    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, highlightthickness=0)
        self.vertical = ttk.Scrollbar(parent, orient='vertical', command=self.canvas.yview)
        self.horizontal = ttk.Scrollbar(parent, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vertical.set, xscrollcommand=self.horizontal.set)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.vertical.grid(row=0, column=1, sticky='ns')
        self.horizontal.grid(row=1, column=0, sticky='ew')
        parent.grid_rowconfigure(0, weight=1); parent.grid_columnconfigure(0, weight=1)
        self.content = tk.Frame(self.canvas, padx=10, pady=10)
        self.window = self.canvas.create_window(0, 0, window=self.content, anchor='nw')
        self.content.bind('<Configure>', self.resize)
        self.canvas.bind('<Configure>', self.resize)
        # Bind only this form's controls; never intercept other dialogs or logs.

    def resize(self, event=None):
        width = max(self.content.winfo_reqwidth(), self.canvas.winfo_width())
        height = max(self.content.winfo_reqheight(), self.canvas.winfo_height())
        size = (width, height)
        if getattr(self, "_size", None) != size:
            self._size = size
            self.canvas.itemconfigure(self.window, width=width, height=height)
            self.canvas.configure(scrollregion=(0, 0, width, height))

    def bind_navigation(self):
        def reveal(event):
            widget = event.widget
            self.canvas.update_idletasks()
            x = widget.winfo_rootx() - self.content.winfo_rootx()
            y = widget.winfo_rooty() - self.content.winfo_rooty()
            width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
            total_w, total_h = self.content.winfo_width(), self.content.winfo_height()
            left, top = self.canvas.canvasx(0), self.canvas.canvasy(0)
            if x < left or x + widget.winfo_width() > left + width:
                self.canvas.xview_moveto(max(0, x - 10) / max(1, total_w))
            if y < top or y + widget.winfo_height() > top + height:
                self.canvas.yview_moveto(max(0, y - 10) / max(1, total_h))
        def wheel(event):
            if isinstance(event.widget, (tk.Text, ttk.Combobox)):
                return
            amount = -1 if getattr(event, 'num', 0) == 4 or event.delta > 0 else 1
            if event.state & 1:
                self.canvas.xview_scroll(amount * 3, 'units')
            else:
                self.canvas.yview_scroll(amount * 3, 'units')
            return 'break'
        for widget in descendants(self.content):
            widget.bind('<FocusIn>', reveal, add='+')
            widget.bind('<MouseWheel>', wheel, add='+')
            widget.bind('<Button-4>', wheel, add='+')
            widget.bind('<Button-5>', wheel, add='+')


def descendants(widget):
    yield widget
    for child in widget.winfo_children():
        yield from descendants(child)


def apply_palette(root, bg, fg, entry, button, preview=None, danger=()):
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=bg, foreground=fg, fieldbackground=entry)
    style.configure('TNotebook', background=bg)
    style.configure('TNotebook.Tab', padding=(14, 8))
    style.map('TNotebook.Tab', background=[('selected', entry)], foreground=[('selected', fg)])
    style.map('TCombobox', fieldbackground=[('readonly', entry), ('disabled', bg)],
              foreground=[('disabled', '#87939f'), ('readonly', fg)], selectbackground=[('readonly', button)])
    style.configure('TProgressbar', background=button, troughcolor=entry)
    style.configure('TButton', background=button, foreground='white', padding=(8, 5))
    style.map('TButton', background=[('disabled', entry), ('active', '#256799')],
              foreground=[('disabled', '#87939f'), ('active', 'white')])
    style.configure('Danger.TButton', background='#a52c30', foreground='white')
    style.map('Danger.TButton', background=[('disabled', entry), ('active', '#7e2023')])
    for widget in danger:
        widget.configure(style='Danger.TButton')
    root.option_add('*TCombobox*Listbox.background', entry)
    root.option_add('*TCombobox*Listbox.foreground', fg)
    black = set(descendants(preview)) if preview is not None else set()
    for widget in descendants(root):
        if isinstance(widget, ttk.Widget):
            continue
        options = widget.keys()
        colors = {'background': 'black' if widget in black else bg,
                  'foreground': 'white' if widget in black else fg}
        if isinstance(widget, (tk.Entry, tk.Text)):
            colors.update(background=entry, insertbackground=fg, selectbackground=button,
                          selectforeground='white', disabledbackground=bg, disabledforeground='#87939f')
        if isinstance(widget, tk.Button):
            colors.update(background='#a52c30' if widget in danger else button, foreground='white',
                          activebackground='#7e2023' if widget in danger else entry, activeforeground=fg)
        if isinstance(widget, (tk.Checkbutton, tk.Radiobutton)):
            colors.update(selectcolor=entry, activebackground=bg, activeforeground=fg)
        widget.configure(**{key: value for key, value in colors.items() if key in options})
