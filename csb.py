#!/usr/bin/python

from __future__ import with_statement

import sqlite3
import curses
import sys
import signal
import os
import csui
import math
import re
from optparse import OptionParser
import logging
import tempfile

version = "0.1"


# these should be part of their libraries

class screen:
    def __init__(self):
        self.stdscr = None

    def __enter__(self):
        self.stdscr = curses.initscr()
        self.stdscr.keypad(1)
        curses.noecho()
        return self.stdscr

    def __exit__(self, type, value, traceback):
        curses.nocbreak()
        if self.stdscr:
            self.stdscr.keypad(0)
        curses.echo()
        curses.endwin()

class dbcur:
    def __init__(self, fname):
        self.fname = fname

    def __enter__(self):
        self.conn = sqlite3.connect(self.fname)
        self.c = self.conn.cursor()
        return self.c

    def __exit__(self, type, value, traceback):
        self.c.close()
        self.conn.commit()
        self.conn.close()

def do_outside_curses(func, *args, **kw):
    """
    Leave curses mode before running a function, then re-enter it afterwards.

    Useful when eg you want to see the output of a command line app.
    """
    value = None
    old_winch = signal.signal(signal.SIGWINCH, signal.SIG_IGN)
    old_cursor = csui.curs_set(1)
    curses.endwin()

    try:
        os.system("clear")
        value = func(*args, **kw)
        os.system("clear")
    except Exception, e:
        print "The thing outside curses had an error"
        value = None

    curses.initscr()
    csui.curs_set(old_cursor)

    # bug in python? old_winch = 0, so we have to define our own :-/
    def __sigwinch_handler(signal, frame):
        curses.endwin()
        curses.initscr()
    signal.signal(signal.SIGWINCH, __sigwinch_handler)
    return value


# these should be part of some standard library

def limit(val, a, b):
    n = min(a, b)
    x = max(a, b)
    if val < n: val = n
    if val > x: val = x
    return val

def rowsafe(text):
    #return re.sub("[^a-zA-Z0-9,\.<>\(\)\{\}'\"#@~:;/\\ ]", "", unicode(text).encode('ascii', 'ignore'))
    return re.sub("[^\S ]", "", unicode(text).encode('ascii', 'ignore'))

# database abstraction

def get_tables(cur):
    res = cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = list([row[0] for row in res])
    return tables

# functions

def edit_external(value):
    (fd, tmpname) = tempfile.mkstemp(prefix="csb")
    os.close(fd)

    if value:
        fp = file(tmpname, "w")
        fp.write(value)
        fp.close()

    editor = "vim"
    if 'EDITOR' in os.environ:
        editor = os.environ['EDITOR']
    do_outside_curses(os.system, editor+" "+tmpname)

    fp = file(tmpname, "r")
    value = fp.read()
    fp.close()

    os.unlink(tmpname)

    return value

def edit(stdscr, cur, table, col_names, col_values, col, external):
    if external:
        val = edit_external(col_values[col_names.index(col)])
    else:
        val = csui.get_string(stdscr, "New value for "+col+" column")
    limiter = " AND ".join(["(%s=? OR %s IS NULL)" % (a, a) for a in col_names])
    query = "UPDATE %s SET %s=? WHERE %s" % (table, col, limiter)
    args = [val, ]
    args.extend(col_values)
    cur.execute(query, args)
    logging.info(query)

def main(args):
    parser = OptionParser()
    #parser.add_option("-w", "--width", dest="width", default=20,
    #        help="set the default column width to COLS", metavar="COLS")
    parser.add_option("-t", "--table", dest="table", default=None,
            help="start with table NAME open", metavar="NAME")
    parser.add_option("-d", "--debug",
            action="store_true", dest="debug", default=False,
            help="turn on debugging to csb.log")
    parser.add_option("-y",
            action="store_true", dest="yes", default=False,
            help="Do things without confirming")
    (options, args) = parser.parse_args()

    if len(args) == 1:
        fname = args[0]
    else:
        fname = raw_input("Enter database path: ")
    table = options.table
    yes = options.yes

    if options.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s %(levelname)-8s %(message)s',
            filename="csb.log"
            )
    else:
        logging.basicConfig(
            level=logging.CRITICAL,
            format='%(asctime)s %(levelname)-8s %(message)s'
            )

    with dbcur(fname) as cur:
        tables = get_tables(cur)
        if len(tables) == 0:
            print "No tables in database"
            return 1
        if not table or table not in tables:
            table = get_tables(cur)[0]
        with screen() as stdscr:
            csui.curs_set(False)
            curses.mousemask(0xFFFFFFF)

            selected_row = 0
            selected_col = 0
            page = 0
            page_count = 0
            col_names = []
            last_query = None
            grid = None
            while True:
                #=============================================================
                # calculate useful things

                (height, width) = stdscr.getmaxyx()
                page_size = height-3

                #=============================================================
                # get data

                query = "SELECT * FROM %s LIMIT %d,%d" % (table, page_size*page, page_size)
                if query != last_query:
                    page_count = math.ceil(float(list(cur.execute("SELECT count(*) FROM %s" % table))[0][0]) / page_size)
                    res = list(cur.execute(query))
                    row_count = len(res)
                    col_names = [tup[0] for tup in cur.description]
                    last_query = query
                    col_width = width/len(col_names)

                #=============================================================
                # draw data

                # magic
                title = "CSB %s -- %s (%s) -- Page %d/%d" % (version, fname, table, page+1, page_count)
                csui.set_title(title)
                stdscr.refresh() # this makes things work and I don't know why

                # title
                titlebar = curses.newwin(1, width, 0, 0)
                titlebar.addstr(0, 0, "")
                titlebar.clrtoeol()
                titlebar.addstr(0, (width-len(title))/2, title)
                titlebar.refresh()

                # grid
                grid = curses.newwin(height-1, width, 1, 0)
                _w = str(col_width-1)
                col_fmt = " %-"+_w+"."+_w+"s"
                #csui.draw_border(grid, "moo")
                for colid, col in enumerate(col_names):
                    grid.addstr(0, colid*col_width, col_fmt % col, curses.A_UNDERLINE)
                for rowid, row in enumerate(res):
                    for colid, col in enumerate(row):
                        if rowid == selected_row and colid == selected_col:
                            grid.addstr(rowid+1, colid*col_width, col_fmt % rowsafe(col), curses.A_REVERSE)
                        else:
                            grid.addstr(rowid+1, colid*col_width, col_fmt % rowsafe(col))
                grid.refresh()

                #=============================================================
                # input

                c = stdscr.getch()
                # global
                if c == ord('q'): break
                elif c == ord('t'):
                    tables = get_tables(cur)
                    table = tables[csui.choose_option(stdscr, "Pick a table", tables)]
                # grid
                elif c == curses.KEY_UP:    selected_row = selected_row - 1
                elif c == curses.KEY_DOWN:  selected_row = selected_row + 1
                elif c == curses.KEY_LEFT:  selected_col = selected_col - 1
                elif c == curses.KEY_RIGHT: selected_col = selected_col + 1
                elif c == curses.KEY_MOUSE:
                    (mouseid, x, y, z, bstate) = curses.getmouse()
                    selected_col = x/col_width
                    selected_row = y-2
                # page
                elif c == curses.KEY_PPAGE or c == ord('k'): page = page - 1
                elif c == curses.KEY_NPAGE or c == ord('j'): page = page + 1
                # display
                elif c == ord('+'): col_width = col_width + 1
                elif c == ord('-'): col_width = col_width - 1
                # editing
                elif c == ord('e'):
                    edit(
                        stdscr, cur, table, col_names,
                        res[selected_row],
                        col_names[selected_col], False)
                    last_query = None
                elif c == ord('E'):
                    edit(
                        stdscr, cur, table, col_names,
                        res[selected_row],
                        col_names[selected_col], True)
                    last_query = None
                elif c == curses.KEY_IC or c == ord('i'):
                    vals = []
                    qs = []
                    for col in col_names:
                        vals.append(csui.get_string(stdscr, col))
                        qs.append("?")
                    query = "INSERT INTO %s(%s) VALUES (%s)" % (table, ", ".join(col_names), ", ".join(qs))
                    if yes or csui.confirm(stdscr, [query, str(vals)]):
                        cur.execute(query, vals)
                        last_query = None
                elif c == curses.KEY_DC or c == ord('d'):
                    # FIXME: SQL injection
                    query = "DELETE FROM %s WHERE %s='%s'" % (table, col_names[selected_col], res[selected_row][selected_col])
                    if yes or csui.confirm(stdscr, query):
                        cur.execute(query)
                        last_query = None
                elif c == curses.KEY_ENTER or c == ord('v'):
                    csui.alert(stdscr, "Viewing Row",
                        [str(a)+": "+unicode(b) for a, b in zip(col_names, res[selected_row])]
                    )
                elif c == ord('h') or c == ord('?'):
                    csui.alert(stdscr, "Keys", [
                        "t       - select table",
                        "arrows  - move over grid",
                        "mouse1  - select cell"
                        "pgup/dn - switch pages",
                        "+ / -   - resize columns",
                        "e       - edit cell",
                        "E       - edit in external editor",
                        "d       - delete row",
                        "v       - view row",
                        "h / ?   - help",
                        "q       - quit",
                    ])

                #=============================================================
                # side effects of input

                if selected_row < 0:
                    if page == 0:
                        selected_row = 0
                    else:
                        page = page - 1
                        selected_row = row_count-1
                if selected_row > row_count - 1:
                    if page == page_count - 1:
                        selected_row = row_count-1
                    else:
                        page = page + 1
                        selected_row = 0
                selected_col = limit(selected_col, 0, len(col_names)-1)
                page = limit(page, 0, page_count-1)
                col_width = limit(col_width, 5, 100)

    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv))

