# -*- coding: utf-8 -*-

import re
import curses
import curses.textpad
import six
import math
import cmd

from itertools import islice

from percol import display, debug

class SelectorView(object):
    def __init__(self, percol = None):
        self.percol  = percol
        self.screen  = percol.screen
        self.display = percol.display
        self.fold_fields = []

    CANDIDATES_LINE_BASIC    = ("on_default", "default")
    CANDIDATES_LINE_SELECTED = ("underline", "on_magenta", "white")
    CANDIDATES_LINE_MARKED   = ("bold", "on_cyan", "black")
    CANDIDATES_LINE_QUERY    = ("yellow", "bold")
    MESSAGE_ERROR            = ("on_red", "white")
    FIELD_SEP                = ' >< '
    FOLDED                   = '..'
    STACKLINE                = '========= Command Stack ========='

    @property
    def RESULTS_DISPLAY_MAX(self):
        return self.display.Y_END - self.display.Y_BEGIN - (len(self.model.stack) + 1)

    @property
    def model(self):
        return self.percol.model

    @property
    def page_number(self):
        return int(self.model.index / self.RESULTS_DISPLAY_MAX) + 1

    @property
    def total_page_number(self):
        return max(int(math.ceil(1.0 * self.model.results_count / self.RESULTS_DISPLAY_MAX)), 1)

    @property
    def absolute_page_head(self):
        return self.RESULTS_DISPLAY_MAX * int(self.model.index / self.RESULTS_DISPLAY_MAX)

    @property
    def absolute_page_tail(self):
        return self.absolute_page_head + self.RESULTS_DISPLAY_MAX

    def refresh_display(self):
        with self.percol.global_lock:
            self.display.erase()
            self.display_results()
            self.display_stack()
            self.display_prompt()
            if self.model.query_mode == False:
                self.stack_fname_prompt()
            self.display.refresh()

    def display_line(self, y, x, s, style = None):
        if style is None:
            style = self.CANDIDATES_LINE_BASIC
        self.display.add_aligned_string(s, y_offset = y, x_offset = x, style = style, fill = True)

    def get_spans(self, line, sep):
        spans = []
        
        reg = re.compile (sep)
        last_end = 0
        for m in reg.finditer(line):
            spans.append((last_end,m.start()-1))
            last_end = m.start()+len(m.group(0))
        spans.append((last_end,len(line)))

        return spans
        

    def fold_line(self, orig_str, sep, fold_fields):
        '''
        Fold folded line for specified fields
        '''
        new_line = orig_str
        if fold_fields:
            fields = orig_str.split(sep)
            new_line = ''
            lst = [x for x in range(len(fields))]

            for x in lst[:-1]:
                if x in fold_fields:
                    new_line += self.FOLDED+sep
                else:
                    new_line += fields[x]+sep

            if len(fields)-1 in fold_fields:
                new_line += self.FOLDED
            else:
                new_line += fields[-1]

        return new_line

    
    def fold_matches(self, old_spans, new_spans, subq, match_info, folded_fields, fold_subq):
        '''
        Check if search string is in a folded field and return modified co-ords for 
        highligting
        '''

        new_match_info = []
        if len(folded_fields) == 0 or match_info == [(0,0)]:
            return match_info

        for x_offset, subq_len in match_info:
            i = 0
            shortened_by = 0
            new_x_offset = x_offset
            new_subq_len = subq_len
            for sp in old_spans:
                if x_offset >= sp[0] and x_offset+subq_len-1 <= sp[1] and i in folded_fields:
                    new_x_offset = new_spans[i][0]
                    shortened_by = 0
                    new_subq_len = len(fold_subq)
                    new_match_info.append((new_x_offset,new_subq_len))
                elif i in folded_fields and x_offset > sp[1]:
                    shortened_by += sp[1] - sp[0] - len(fold_subq) + 1
                i += 1
            new_match_info.append((new_x_offset-shortened_by,new_subq_len))
        return new_match_info


    def display_result(self, y, result, is_current = False, is_marked = False):
        line, find_info, abs_idx = result

        if is_current:
            line_style = self.CANDIDATES_LINE_SELECTED
        elif is_marked:
            line_style = self.CANDIDATES_LINE_MARKED
        else:
            line_style = self.CANDIDATES_LINE_BASIC

        keyword_style = self.CANDIDATES_LINE_QUERY + line_style

        # self.fold_fields = [1,2]

        new_line = self.fold_line(line,self.FIELD_SEP,self.fold_fields)

        self.display_line(y, 0, new_line, style = line_style)

        spans = self.get_spans(line, self.FIELD_SEP)
        new_spans = self.get_spans(new_line, self.FIELD_SEP)

        if find_info is None:
            return
        for (subq, match_info) in find_info:
            new_match_info = self.fold_matches(spans, new_spans, subq, match_info, self.fold_fields, self.FOLDED)
            for x_offset, subq_len in new_match_info:
            # for x_offset, subq_len in match_info:
                try:
                    x_offset_real = display.screen_len(new_line, beg = 0, end = x_offset)

                    self.display.add_string(new_line[x_offset:x_offset + subq_len],
                                            pos_y = y,
                                            pos_x = x_offset_real,
                                            style = keyword_style)
                    
                    # debug.log((line,x_offset,x_offset+subq_len,y,x_offset_real))
                    # debug.log(line[x_offset:x_offset + subq_len])
                    # x_offset_real = display.screen_len(line, beg = 0, end = x_offset)
                    # self.display.add_string(line[x_offset:x_offset + subq_len],
                    #                         pos_y = y,
                    #                         pos_x = x_offset_real,
                    #                         style = keyword_style)
                except curses.error as e:
                    debug.log("addnstr", str(e) + " ({0})".format(y))

    def display_error_message(self, message):
        self.display_line(self.RESULTS_OFFSET_V, 0, message, style=self.MESSAGE_ERROR)

    def display_results(self):
        result_vertical_pos = self.RESULTS_OFFSET_V
        result_pos_direction = 1 if self.results_top_down else -1

        results_in_page = islice(enumerate(self.model.results), self.absolute_page_head, self.absolute_page_tail)

        try:
            for cand_nth, result in results_in_page:
                try:
                    self.display_result(result_vertical_pos, result,
                                        is_current = cand_nth == self.model.index,
                                        is_marked = self.model.get_is_marked(cand_nth))
                except curses.error as e:
                    debug.log("display_results", str(e))
                result_vertical_pos += result_pos_direction
        except Exception as e:
            # debug.log("display_results", str(e))
            debug.log("display_results",
                      six.text_type(" | ".join(
                          map(lambda key: six.text_type(key) +
                              ": "
                              + six.text_type(e.__getattribute__(key)),
                              dir(e)
                          ))
                      ))
            exception_raw_string = str(e).decode(self.percol.encoding) if six.PY2 else str(e)
            self.display_error_message("Error at line " + str(cand_nth) + ": " + exception_raw_string)

    def display_stack(self):
        stack_vertical_pos = self.RESULTS_OFFSET_V + self.RESULTS_DISPLAY_MAX
        result_pos_direction = 1 if self.results_top_down else -1

        self.display.add_string(self.STACKLINE,pos_y=stack_vertical_pos)
        stack_vertical_pos += result_pos_direction
        for command in self.model.stack:
            self.display.add_string(command, pos_y = stack_vertical_pos, pos_x = 0)
            stack_vertical_pos += result_pos_direction
            pass


    results_top_down = True

    @property
    def RESULTS_OFFSET_V(self):
        if self.results_top_down:
            # top -> bottom
            if self.prompt_on_top:
                return self.display.Y_BEGIN + 1
            else:
                return self.display.Y_BEGIN
        else:
            # bottom -> top
            if self.prompt_on_top:
                return self.display.Y_END
            else:
                return self.display.Y_END - 1

    # ============================================================ #
    # Prompt
    # ============================================================ #

    prompt_on_top = True

    @property
    def PROMPT_OFFSET_V(self):
        if self.prompt_on_top:
            return self.display.Y_BEGIN
        else:
            return self.display.Y_END

    PROMPT  = u"QUERY> %q"
    RPROMPT = u"(%i/%I) [%n/%N]"

    def do_display_prompt(self, format,
                          y_offset = 0, x_offset = 0,
                          y_align = "top", x_align = "left"):
        parsed = self.display.markup_parser.parse(format)
        offset = 0
        tokens = []

        self.last_query_position = -1

        for s, attrs in parsed:
            formatted_string = self.format_prompt_string(s, offset)
            tokens.append((formatted_string, attrs))
            offset += display.screen_len(formatted_string)

        y, x = self.display.add_aligned_string_tokens(tokens,
                                                      y_offset = y_offset,
                                                      x_offset = x_offset,
                                                      y_align = y_align,
                                                      x_align = x_align)

        # when %q is specified, record its position
        if self.last_query_position >= 0:
            self.caret_x = self.last_query_position + x 
            self.caret_y = self.PROMPT_OFFSET_V

    def display_prompt(self):
        self.caret_x = -1
        self.caret_y = -1

        self.do_display_prompt(self.RPROMPT,
                               y_offset = self.PROMPT_OFFSET_V,
                               x_align = "right")

        self.do_display_prompt(self.PROMPT,
                               y_offset = self.PROMPT_OFFSET_V)

        try:
            # move caret
            if self.caret_x >= 0 and self.caret_y >= 0:
                self.screen.move(self.caret_y , 
                                 self.caret_x  + display.screen_len(self.model.query, 0, self.model.caret))
        except curses.error:
            pass

    def maketextbox(self,h,w,y,x,value="",deco=None,textColorpair=0,decoColorpair=0):
        # thanks to http://stackoverflow.com/a/5326195/8482 for this
        nw = curses.newwin(h,w,y,x)
        txtbox = curses.textpad.Textbox(nw,insert_mode=True)
        if deco=="frame":
            self.screen.attron(decoColorpair)
            curses.textpad.rectangle(self.screen,y-1,x-1,y+h,x+w)
            self.screen.attroff(decoColorpair)
        elif deco=="underline":
            self.screen.hline(y+1,x,underlineChr,w,decoColorpair)

        nw.addstr(0,0,value,textColorpair)
        nw.attron(textColorpair)
        self.screen.refresh()
        return nw,txtbox


    def stack_fname_prompt(self):
        win_y = self.RESULTS_DISPLAY_MAX + 1
        win_x = len(self.STACKLINE)
        
        curses.noecho()
        textwin,textbox = self.maketextbox(1,40, win_y,win_x,"")
        
        flag = False
        textbox.edit()
        text = textbox.gather()
        debug.log("Filename: %s"%text)
        
        # self.screen.refresh()
        # self.display.refresh()
        # while not flag :
            # curses.beep()
            # flag = Commands().onecmd(text)


    def handle_format_prompt_query(self, matchobj, offset):
        # -1 is from first '%' of %([a-zA-Z%])
        self.last_query_position = matchobj.start(1) - 1 + offset
        return self.model.query

    prompt_replacees = {
        "%" : lambda self, **args: "%",
        # display query and caret
        "q" : lambda self, **args: self.handle_format_prompt_query(args["matchobj"], args["offset"]),
        # display query but does not display caret
        "Q" : lambda self, **args: self.model.query,
        "n" : lambda self, **args: self.page_number,
        "N" : lambda self, **args: self.total_page_number,
        "i" : lambda self, **args: self.model.index + (1 if self.model.results_count > 0 else 0),
        "I" : lambda self, **args: self.model.results_count,
        "c" : lambda self, **args: self.model.caret,
        "k" : lambda self, **args: self.percol.last_key
    }

    format_pattern = re.compile(u'%([a-zA-Z%])')
    def format_prompt_string(self, s, offset = 0):
        def formatter(matchobj):
            al = matchobj.group(1)
            if al in self.prompt_replacees:
                res = self.prompt_replacees[al](self, matchobj = matchobj, offset = offset)
                return (res if isinstance(res, six.text_type)
                        else six.text_type(res))
            else:
                return u""

        return re.sub(self.format_pattern, formatter, s)
