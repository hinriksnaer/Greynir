"""
    Reynir: Natural language processing for Icelandic

    Grammar module

    Author: Vilhjalmur Thorsteinsson

    This software is at a very early development stage.
    While that is the case, it is:
    Copyright (c) 2015 Vilhjalmur Thorsteinsson
    All rights reserved

    A grammar is specified as a set of rules. Each rule has a single
    left-hand-side nonterminal, associated with 1..n right-hand-side
    productions. Each right-hand-side production is a sequence of
    nonterminals and terminals. A terminal can match a token of
    input.

    In Reynir grammars, nonterminals always start with an uppercase letter.
    Terminals may be identifiers starting with lowercase letters, or
    literals enclosed within single or double quotes. Epsilon (empty)
    productions are allowed and denoted by 0.

"""

import codecs

from pprint import pprint as pp


class GrammarError(Exception):

    """ Exception class for errors in a grammar """

    def __init__(self, text, fname = None, line = 0):

        """ A GrammarError contains an error text and optionally the name
            of a grammar file and a line number where the error occurred """

        self.fname = fname
        self.line = line
        prefix = ""
        if line:
            prefix = "Line " + str(line) + ": "
        if fname:
            prefix = fname + " - " + prefix
        Exception.__init__(self, prefix + text)


class Nonterminal:

    """ A nonterminal, either at the left hand side of
        a rule or within a production """

    def __init__(self, name, fname = None, line = 0):
        self.name = name
        # Place of initial definition in a grammar file
        self._fname = fname
        self._line = line
        # Has this nonterminal been referenced in a production?
        self._ref = False

    def add_ref(self):
        """ Mark this as being referenced """
        self._ref = True

    def has_ref(self):
        """ Return True if the nonterminal has been referenced in a production """
        return self._ref

    def fname(self):
        """ Return the name of the grammar file where this nonterminal was defined """
        return self._fname

    def line(self):
        """ Return the number of the line within the grammar file where this nt was defined """
        return self._line

    def __eq__(self, other):
        return isinstance(other, Nonterminal) and self.name == other.name

    def __ne__(self, other):
        return not isinstance(other, Nonterminal) or self.name != other.name

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return '<{0}>'.format(self.name)

    def __str__(self):
        return '<{0}>'.format(self.name)


class Terminal:

    """ A terminal within a right-hand-side production """

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return '{0}'.format(self.name)

    def __str__(self):
        return '{0}'.format(self.name)

    def matches(self, t_kind, t_val):
        # print("Terminal.matches: self.name is {0}, t_kind is {1}".format(self.name, t_kind))
        return self.name == t_kind


class LiteralTerminal(Terminal):

    """ A literal (constant string) terminal within a right-hand-side production """

    def __init__(self, lit):
        Terminal.__init__(self, lit)

    def matches(self, t_kind, t_val):
        """ A literal terminal matches a token if the token text is identical to the literal """
        return self.name == t_val

    def __repr__(self):
        return '\'{0}\''.format(self.name)

    def __str__(self):
        return '\'{0}\''.format(self.name)


class Token:

    """ A token from the input stream tokenizer """

    def __init__(self, kind, val):
        """ A basic token has a kind and a value, both strings """
        self.kind = kind
        self.val = val

    def __repr__(self):
        """ Return a simple string representation of this token """
        if self.kind == self.val:
            return '{0}'.format(self.kind)
        return '{0}:{1}'.format(self.kind, self.val)

    def matches(self, terminal):
        """ Does this token match the given terminal? """
        # By default, ask the terminal
        return terminal.matches(self.kind, self.val)


class Production:

    """ A right-hand side of a grammar rule """

    _INDEX = 0 # Running sequence number of all productions

    def __init__(self, fname = None, line = 0, rhs = None):

        """ Initialize a production from a list of
            right-hand-side nonterminals and terminals """

        self._rhs = [] if rhs is None else rhs
        # If parsing a grammar file, note the position of the production
        # in the file
        self._fname = fname
        self._line = line
        # Give all productions a unique sequence number for hashing purposes
        self._index = Production._INDEX
        Production._INDEX += 1

    def __hash__(self):
        """ Use the index of this production as a basis for the hash """
        return self._index.__hash__()

    def __eq__(self, other):
        return isinstance(other, Production) and self._index == other._index

    def __ne__(self, other):
        return not isinstance(other, Production) or self._index != other._index

    def append(self, t):
        """ Append a terminal or nonterminal to this production """
        self._rhs.append(t)

    def expand(self, l):
        """ Add a list of terminals and/or nonterminals to this production """
        self._rhs.expand(l)

    def length(self):
        """ Return the length of this production """
        return len(self._rhs)

    def is_empty(self):
        """ Return True if this is an empty (epsilon) production """
        return len(self._rhs) == 0

    def fname(self):
        return self._fname

    def line(self):
        return self._line

    def __getitem__(self, index):
        """ Return the terminal or nonterminal at the given index position """
        return self._rhs[index]

    def __len__(self):
        """ Return the length of this production """
        return len(self._rhs)

    def __repr__(self):
        """ Return a representation of this production """
        return "<Production: " + repr(self._rhs) + ">"

    def __str__(self):
        """ Return a representation of this production """
        return " ".join([str(t) for t in self._rhs]) if self._rhs else "0"


class Grammar:

    """
        A grammar maps nonterminals to a list of right hand sides.
        Each right hand side is a list of terminals and nonterminals.

        The text representation of a grammar is as follows:

        A -> A B terminal C
            | A '/' D
            | 0
        B -> terminal "+" C

        Nonterminals start with uppercase letters.

        Terminals start with lowercase letters or are enclosed
        in single or double quotes.

        0 means an empty (epsilon) production.

    """

    def __init__(self):
        self._nonterminals = { }
        self._terminals = { }
        self._grammar = { }
        self._root = None

    def grammar(self):
        """ Return the raw grammar dictionary, Nonterminal -> Production """
        return self._grammar

    def root(self):
        """ Return the root nonterminal for this grammar """
        return self._root

    def __str__(self):

        def to_str(plist):
            return " | ".join([str(p) for p in plist])

        return "".join([str(nt) + " → " + to_str(plist) + "\n" for nt, plist in self._grammar.items()])

    def read(self, fname):
        """ Read grammar from a text file """

        # Shortcuts
        terminals = self._terminals
        nonterminals = self._nonterminals
        grammar = self._grammar
        line = 0

        try:
            with codecs.open(fname, "r", "utf-8") as inp:
                # Read grammar file line-by-line
                current_NT = None
                for s in inp:
                    line += 1
                    # Ignore comments
                    ix = s.find('#')
                    if ix >= 0:
                        s = s[0:ix]
                    s = s.strip()
                    if not s:
                        # Blank line: ignore
                        continue

                    def _add_rhs(nt, rhs):
                        """ Add a right-hand-side production to a nonterminal rule """
                        if nt not in grammar:
                            grammar[nt] = [ ] if rhs is None else [ rhs ]
                            return
                        if rhs is None:
                            return
                        if rhs.is_empty():
                            # Adding epsilon production: avoid multiple ones
                            if any(p.is_empty() for p in grammar[nt]):
                                return
                        grammar[nt].append(rhs)

                    def _parse_rhs(nt, s):
                        """ Parse a right-hand side sequence """
                        s = s.strip()
                        if not s:
                            return
                        rhs = s.split()
                        result = Production(fname, line)
                        for r in rhs:
                            if r == "0":
                                # Empty (epsilon) production
                                if len(rhs) != 1:
                                    raise GrammarError("Empty (epsilon) rule must be of the form NT -> 0", fname, line)
                                break
                            repeat = None
                            if r[-1] in '*+?':
                                # Optional repeat/conditionality specifier
                                # Asterisk: Can be repeated 0 or more times
                                # Plus: Can be repeated 1 or more times
                                # Question mark: optionally present once
                                repeat = r[-1]
                                r = r[0:-1]
                            if r[0] in "\"'":
                                # Literal terminal symbol
                                sym = r
                                lit = r[1:-1]
                                if sym not in terminals:
                                    terminals[sym] = LiteralTerminal(lit)
                                n = terminals[sym]
                            else:
                                if not r.isidentifier():
                                    raise GrammarError("Invalid identifier '{0}'".format(r), fname, line)
                                if r[0].isupper():
                                    # Reference to nonterminal
                                    if r not in nonterminals:
                                        nonterminals[r] = Nonterminal(r, fname, line)
                                    nonterminals[r].add_ref() # Note that the nonterminal has been referenced
                                    n = nonterminals[r]
                                else:
                                    # Identifier of terminal
                                    if r not in terminals:
                                        terminals[r] = Terminal(r)
                                    n = terminals[r]
                            # If the production item can be repeated,
                            # create a new production and substitute.
                            # A -> B C* D becomes:
                            # A -> B C_new_* D
                            # C_new_* -> C_new_* C | 0
                            # A -> B C+ D becomes:
                            # A -> B C_new_+ D
                            # C_new_+ -> C_new_+ C | C
                            # A -> B C? D becomes:
                            # A -> B C_new_? D
                            # C_new_? -> C | 0
                            if repeat is not None:
                                new_nt_id = r + repeat
                                # Make the new nonterminal and production if not already there
                                if new_nt_id not in nonterminals:
                                    new_nt = nonterminals[new_nt_id] = Nonterminal(new_nt_id, fname, line)
                                    new_nt.add_ref()
                                    # First production: C_new_x C
                                    new_p = Production(fname, line)
                                    if repeat != '?':
                                        new_p.append(new_nt) # C_new_x
                                    new_p.append(n) # C
                                    _add_rhs(new_nt, new_p)
                                    # Second production: epsilon(*, ?) or C(+)
                                    new_p = Production(fname, line)
                                    if repeat == '+':
                                        new_p.append(n)
                                    _add_rhs(new_nt, new_p)
                                # Substitute the C_new_x in the original production
                                n = nonterminals[new_nt_id]
                            result.append(n)
                        if result.length() == 1 and result[0] == current_NT:
                            # Nonterminal derives itself
                            raise GrammarError("Nonterminal {0} deriving itself".format(current_NT), fname, line)
                        _add_rhs(nt, result)

                    if s.startswith('|'):
                        # Alternative to previous nonterminal rule
                        if current_NT is None:
                            raise GrammarError("Missing nonterminal", fname, line)
                        _parse_rhs(current_NT, s[1:])
                    else:
                        if "→" in s:
                            # Fancy schmancy arrow sign: use it
                            rule = s.split("→", maxsplit=1)
                        else:
                            rule = s.split("->", maxsplit=1)
                        nt = rule[0].strip()
                        if not nt.isidentifier():
                            raise GrammarError("Invalid nonterminal name '{0}' in grammar".format(nt), fname, line)
                        if nt not in nonterminals:
                            nonterminals[nt] = Nonterminal(nt, fname, line)
                        current_NT = nonterminals[nt]
                        if self._root is None:
                            # Remember first nonterminal as the root
                            self._root = current_NT
                            self._root.add_ref() # Implicitly referenced
                        if current_NT not in grammar:
                            grammar[current_NT] = [ ]
                        if len(rule) >= 2:
                            # We have a right hand side: add a grammar rule
                            _parse_rhs(current_NT, rule[1])

        except (IOError, OSError):
            raise GrammarError("Unable to open or read grammar file", fname, 0)

        # Check all nonterminals to verify that they have productions and are referenced
        for nt in nonterminals.values():
            if not nt.has_ref():
                raise GrammarError("Nonterminal {0} is never referenced in a production".format(nt), nt.fname(), nt.line())
        for nt, plist in grammar.items():
            if len(plist) == 0:
                raise GrammarError("Nonterminal {0} has no productions".format(nt), nt.fname(), nt.line())
            else:
                for p in plist:
                    if len(p) == 1 and plist[0] == nt:
                        raise GrammarError("Nonterminal {0} produces itself".format(nt), p.fname(), p.line())

        # Check that all nonterminals derive terminal strings
        agenda = [ nt for nt in nonterminals.values() ]
        der_t = set()
        while agenda:
            reduced = False
            for nt in agenda:
                for p in grammar[nt]:
                    if all([True if isinstance(s, Terminal) else s in der_t for s in p]):
                        der_t.add(nt)
                        break
                if nt in der_t:
                    reduced = True
            if not reduced:
                break
            agenda = [ nt for nt in nonterminals.values() if nt not in der_t ]
        if agenda:
            raise GrammarError("Nonterminals {0} do not derive terminal strings"
                .format(", ".join([str(nt) for nt in agenda])), fname, 0)

        # Check that all nonterminals are reachable from the root
        unreachable = { nt for nt in nonterminals.values() }

        def _remove(nt):
            """ Recursively remove all nonterminals that are reachable from nt """
            unreachable.remove(nt)
            for p in grammar[nt]:
                for s in p:
                    if isinstance(s, Nonterminal) and s in unreachable:
                        _remove(s)

        _remove(self._root)

        if unreachable:
            raise GrammarError("Nonterminals {0} are unreachable from the root"
                .format(", ".join([str(nt) for nt in unreachable])), fname, 0)

