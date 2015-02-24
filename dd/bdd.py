"""Ordered binary decision diagrams based on `networkx`.


References
==========

Randal E. Bryant
    "Graph-based algorithms for Boolean function manipulation"
    IEEE Transactions on Computers
    Vol. C-35, No.8, August, 1986, pp.677--690

Karl S. Brace, Richard L. Rudell, Randal E. Bryant
    "Efficient implementation of a BDD package"
    27th ACM/IEEE Design Automation Conference, 1990
    pp.40--45

Christel Baier and Joost-Pieter Katoen
    "Principles of model checking"
    MIT Press, 2008
    section 6.7, pp.381--421

Fabio Somenzi
    "Binary decision diagrams"
    Calculational system design, Vol.173
    NATO Science Series F: Computer and systems sciences
    pp.303--366, IOS Press, 1999

Henrik R. Andersen
    "An introduction to binary decision diagrams"
    Lecture notes for "Efficient Algorithms and Programs", 1999
    The IT University of Copenhagen
"""
import logging
logger = logging.getLogger(__name__)
from collections import Mapping
from itertools import tee, izip
# inline:
# import networkx
# import pydot
# import tulip.spec.lexyacc


class BDD(object):
    """Shared ordered binary decision diagram.

    There must be at least one root.
    The terminal node is 1.
    Complemented edges are represented as negative integers.
    Roots and values returned are edges, possibly complemented.

    Attributes:
      - `ordering`: `dict` mapping `variables` to `int` indices
      - `roots`: nodes with no predecessors
      - `_pred`: `dict` mapping tuples `(index, low, high)` to nodes
      - `_succ`: `dict` mapping nodes to tuples `(index, low, high)`
    where index, low, high are `int`.

    If `ordering` changes, then reordering is needed.

    To ensure `ite` maintains reducedness add new
    nodes using `find_or_add` to keep the table updated,
    or call `update_pairs_table` prior to calling `ite`.
    """

    def __init__(self, ordering=None, **kw):
        self._pred = dict()  # (i, low, high) -> u
        self._succ = dict()  # u -> (i, low, high)
        self._ite_table = dict()  # (cond, high, low)
        if ordering is None:
            ordering = dict()
        else:
            i = len(ordering)
            self._succ[1] = (i, None, None)
        self.ordering = ordering
        self.roots = set()
        self._parser = None

    def __len__(self):
        return len(self._succ)

    def __contains__(self, u):
        return u in self._succ

    def __iter__(self):
        return iter(self._succ)

    def __str__(self):
        return (
            'Binary decision diagram:\n'
            '------------------------\n'
            'var ordering: {self.ordering}\n'
            'roots: {self.roots}\n').format(self=self)

    def _map_to_index(self, d):
        """Map keys of `d` to variable indices.

        If `d` is an iterable but not a mapping,
        then an iterable is returned.
        The mapping is `self.ordering`.
        """
        if not d:
            return d
        # are keys variable names ?
        u = next(iter(d))
        if u not in self.ordering:
            return d
        if isinstance(d, Mapping):
            r = {
                self.ordering[var]: bool(val)
                for var, val in d.iteritems()}
        else:
            r = {self.ordering[k] for k in d}
        return r

    def evaluate(self, u, values):
        """Return value of node `u` for evaluation `values`.

        @param values: (partial) mapping from `variables` to values
            keys can be variable names as `str` or indices as `int`.
            Mapping should be complete with respect to `u`.
        @type values: `dict`
        """
        assert abs(u) in self, u
        values = self._map_to_index(values)
        return self._evaluate(u, values)

    def _evaluate(self, u, values):
        """Recurse to compute value."""
        if abs(u) == 1:
            return u
        i, v, w = self._succ[abs(u)]
        if values[i]:
            r = self._evaluate(w, values)
        else:
            r = self._evaluate(v, values)
        if u < 0:
            return -r
        else:
            return r

    def is_essential(self, u, var):
        """Return `True` if `var` essential for node `u`."""
        i = self.ordering.get(var)
        if i is None:
            return False
        iu, v, w = self._succ[abs(u)]
        # var above node u ?
        if i < iu:
            return False
        if i == iu:
            return True
        # u depends on node labeled with var ?
        if self.is_essential(v, var):
            return True
        if self.is_essential(w, var):
            return True
        return False

    def support(self, u):
        """Return variables that node `u` depends on."""
        var = set()
        self._support(u, var)
        self._ind2var = {k: v for v, k in self.ordering.iteritems()}
        return {self._ind2var[i] for i in var}

    def _support(self, u, var):
        """Recurse to collect variables in support."""
        # exhausted all vars ?
        if len(var) == len(self.ordering):
            return
        # terminal ?
        if u == -1 or u == 1:
            return
        i, v, w = self._succ[abs(u)]
        var.add(i)
        self._support(v, var)
        self._support(w, var)

    def levels(self, skip_terminals=False):
        """Return generator of tuples `(u, i, v, w)`.

        Where `i` ranges from terminals to root
        """
        if skip_terminals:
            n = len(self.ordering) - 1
        else:
            n = len(self.ordering)
        for i in xrange(n, -1, -1):
            for u, (j, v, w) in self._succ.iteritems():
                if i != j:
                    continue
                yield u, i, v, w

    def reduction(self):
        """Return copy reduced with respect to `self.ordering`.

        Not to be used for large BDDs.
        Instead, construct them directly reduced.
        """
        # terminals
        bdd = BDD(self.ordering)
        umap = {-1: -1, 1: 1}
        # non-terminals
        for u, i, v, w in self.levels(skip_terminals=True):
            p, q = umap[v], umap[w]
            r = bdd.find_or_add(i, p, q)
            umap[u] = r
            if u in self.roots:
                bdd.roots.add(r)
        return bdd

    def compose(self, f, j, g, cache=None):
        """Return f(x_j=g).

        @param u, v: nodes
        @param j: variable index
        @param cache: stores intermediate results
        """
        # terminal or exhausted valuation ?
        if abs(f) == 1:
            return f
        # cached ?
        if cache is None:
            cache = dict()
        elif f in cache:
            return cache[f]
        i, v, w = self._succ[abs(f)]
        if j < i:
            return f
        elif i == j:
            r = self.ite(g, w, v)
        else:
            # i < j
            z = min(i, self._succ[abs(g)][0])
            f0, f1 = self._top_cofactor(f, z)
            g0, g1 = self._top_cofactor(g, z)
            p = self.compose(f0, j, g0, cache)
            q = self.compose(f1, j, g1, cache)
            r = self.find_or_add(z, p, q)
        cache[(f, g)] = r
        return r

    def ite(self, g, u, v):
        """Return node for if-then-else of `g`, `u` and `v`.

        @param u: high
        @param v: low
        @type g, u, v: `int`
        @rtype: [BDD]
        """
        # is g terminal ?
        if g == 1:
            return u
        elif g == -1:
            return v
        # g is non-terminal
        # already computed ?
        r = (g, u, v)
        w = self._ite_table.get(r)
        if w is not None:
            return w
        z = min(self._succ[abs(g)][0],
                self._succ[abs(u)][0],
                self._succ[abs(v)][0])
        g0, g1 = self._top_cofactor(g, z)
        u0, u1 = self._top_cofactor(u, z)
        v0, v1 = self._top_cofactor(v, z)
        p = self.ite(g0, u0, v0)
        q = self.ite(g1, u1, v1)
        w = self.find_or_add(z, p, q)
        # cache
        self._ite_table[r] = w
        return w

    def _top_cofactor(self, u, i):
        """Return restriction for assignment to single variable.

        @param u: node
        @param i: variable index
        @param value: assignment to variable `i`
        """
        # terminal node ?
        if abs(u) == 1:
            return (u, u)
        # non-terminal node
        iu, v, w = self._succ[abs(u)]
        # u independent of var ?
        if i < iu:
            return (u, u)
        elif iu < i:
            raise Exception('call `cofactor` instead')
        # iu == i
        # u labeled with var
        # complement ?
        if u < 0:
            v, w = -v, -w
        return (v, w)

    def cofactor(self, u, values):
        """Return restriction of `u` to valuation `values`.

        @param u: node
        @param values: `dict` that maps var indices to values
        """
        values = self._map_to_index(values)
        cache = dict()
        ordvar = sorted(values)
        j = 0
        return self._cofactor(u, j, ordvar, values, cache)

    def _cofactor(self, u, j, ordvar, values, cache):
        """Recurse to compute cofactor."""
        # terminal ?
        if abs(u) == 1:
            return u
        if u in cache:
            return cache[u]
        i, v, w = self._succ[abs(u)]
        n = len(ordvar)
        # skip nonessential variables
        while j < n:
            if ordvar[j] < i:
                j += 1
            else:
                break
        else:
            # exhausted valuation
            return u
        # recurse
        if i in values:
            val = values[i]
            if bool(val):
                v = w
            r = self._cofactor(v, j, ordvar, values, cache)
        else:
            p = self._cofactor(v, j, ordvar, values, cache)
            q = self._cofactor(w, j, ordvar, values, cache)
            r = self.find_or_add(i, p, q)
        # complement ?
        if u < 0:
            r = -r
        cache[u] = r
        return r

    def quantify(self, u, qvars, forall=False):
        """Return existential of universal abstraction.

        Caution: `dvars` is modified.

        @param u: node
        @param qvars: `set` of quantified variables
        @param forall: if `True`,
            then quantify `dvars` universally,
            else quantify existentially.
        """
        qvars = self._map_to_index(qvars)
        cache = dict()
        ordvar = sorted(qvars)
        j = 0
        return self._quantify(u, j, ordvar, qvars, forall, cache)

    def _quantify(self, u, j, ordvar, qvars, forall, cache):
        """Recurse to quantify variables."""
        # terminal ?
        if abs(u) == 1:
            return u
        if u in cache:
            return cache[u]
        i, v, w = self._succ[abs(u)]
        n = len(ordvar)
        # skip nonessential variables
        while j < n:
            if ordvar[j] < i:
                j += 1
            else:
                break
        else:
            # exhausted valuation
            return u
        # recurse
        if i in qvars:
            if forall:
                r = self.ite(v, w, -1)  # conjoin
            else:
                r = self.ite(v, 1, w)  # disjoin
        else:
            p = self._quantify(v, j, ordvar, qvars, forall, cache)
            q = self._quantify(w, j, ordvar, qvars, forall, cache)
            r = self.find_or_add(i, p, q)
        cache[u] = r
        return r

    def find_or_add(self, i, v, w):
        """Return a node at level `i` with successors `v, w`.

        If one exists, it is quickly found in the cached table.

        @param i: level in `range(n_vars - 1)`
        @param v: low
        @param w: high
        """
        assert 0 <= i < len(self.ordering), i
        assert abs(v) in self, v
        assert abs(w) in self, w
        # ensure canonicity of complemented edges
        if w < 0:
            v, w = -v, -w
            r = -1
        else:
            r = 1
        if v == w:
            return r * v
        t = (i, v, w)
        u = self._pred.get(t)
        if u is None:
            u = len(self) + 1
            assert u not in self, u
            self._pred[t] = u
            self._succ[u] = t
        return r * u

    def update_pairs_table(self):
        """Update table that maps (level, low, high) to nodes."""
        for u, t in self._succ.iteritems():
            if abs(u) == 1:
                continue
            self._pred[t] = u

    def sat_len(self, u=None):
        """Return number of models of node `u`.

        Labels nodes with `"sat_len"`, the number of
        satisfying valuations for the corresponding factor.

        The default node `u` is `self.root`, unless
        BDD has multiple roots.
        """
        if u is None:
            if len(self.roots) != 1:
                raise Exception(
                    'No single root defined: give `u`')
            else:
                (root, ) = self.roots
        else:
            root = u
        assert abs(root) in self, root
        i, _, _ = self._succ[abs(root)]
        return self._sat_len(root) * 2**i

    def _sat_len(self, u):
        """Recurse to compute the number of models."""
        # terminal ?
        if u == -1:
            return 0
        elif u == 1:
            return 1
        # non-terminal
        i, v, w = self._succ[abs(u)]
        dv = self._sat_len(v)
        dw = self._sat_len(w)
        iv, _, _ = self._succ[abs(v)]
        iw, _, _ = self._succ[w]
        # complement ?
        du = (dv * 2**(iv - i - 1) +
              dw * 2**(iw - i - 1))
        # complement ?
        if u < 0:
            return 2**(len(self.ordering) - iv) - dv
        else:
            return du

    def sat_iter(self, u=None):
        """Return iterator over models.

        Use `next(BDD.sat_iter())` to get a single
        satisfying valuation.

        If the BDD has multiple roots,
        then a root `u` must be given.

        If a variable is missing from the `dict` of a model,
        then it is a "don't care", i.e., the model can be
        completed by assigning any value to that variable.
        """
        # empty or unsat ?
        if len(self) == 0:
            return
        # satisfiable
        if u is None:
            if len(self.roots) != 1:
                raise Exception(
                    'No single root defined: give `root`')
            else:
                (u, ) = self.roots
        self._ind2var = {k: v for v, k in self.ordering.iteritems()}
        return self._sat_iter(u, '', True)

    def _sat_iter(self, u, path, value):
        """Recurse to enumerate models."""
        if u < 0:
            value = not value
        # terminal ?
        if abs(u) == 1:
            if value:
                model = {self._ind2var[i]: int(val)
                         for i, val in enumerate(path)}
                yield model
            return
        # non-terminal
        _, v, w = self._succ[abs(u)]
        for x in self._sat_iter(v, path + '0', value):
            yield x
        for x in self._sat_iter(w, path + '1', value):
            yield x

    def assert_consistent(self):
        """Raise `AssertionError` if not a valid BDD."""
        assert self.roots  # must be rooted
        for root in self.roots:
            assert abs(root) in self._succ, root
        for u, (i, v, w) in self._succ.iteritems():
            assert isinstance(i, int), i
            # terminal ?
            if v is None:
                assert w is None, w
                continue
            else:
                assert abs(v) in self._succ, v
            if w is None:
                assert v is None, v
                continue
            else:
                assert w >= 0, w  # "high" is regular edge
                assert w in self._succ, w
            # var order should increase
            for x in (v, w):
                ix, _, _ = self._succ[abs(x)]
                assert i < ix, (u, i)
                # roots don't have predecessors
                assert x not in self.roots, self.roots
        return True

    def add_expr(self, e):
        """Return node for expression `e` after adding it.

        @type expr: `str`
        """
        try:
            from tulip.spec import lexyacc
        except ImportError:
            raise Exception('failed to import `tulip.spec`')
        if self._parser is None:
            self._parser = lexyacc.Parser()
        return self.add_ast(self._parser.parse(e))

    def add_ast(self, t):
        """Add abstract syntax tree `t` to `self`.

        The variables must be keys in `self.ordering`.

        Any AST nodes are acceptable provided they have
        attributes:
          - `"operator"` and `"operands"` for operator nodes
          - `"value"` equal to:
            - `"True"` or `"False"` for Boolean constants
            - a key (var name) in `self.ordering` for variables

        @type t: `Terminal` or `Operator` of `tulip.spec.ast`
        """
        # assert 1 in `self`, with index `len(self.ordering)`
        # operator ?
        try:
            operands = map(self.add_ast, t.operands)
            return self.apply(t.operator, *operands)
        except AttributeError:
            # var or bool (terminal AST node)
            # Boolean constant ?
            if t.value in {'True', 'False'}:
                index = len(self.ordering)
                self._succ[1] = (index, None, None)
                u = -1 if t.value == 'False' else 1
                return u
            # variable `t.value` must be in `self.ordering`
            i = len(self.ordering)
            self._succ[1] = (i, None, None)
            j = self.ordering[t.value]
            return self.find_or_add(j, -1, 1)

    def to_expr(self, u):
        """Return a Boolean expression for node `u`."""
        ind2var = {k: v for v, k in self.ordering.iteritems()}
        return self._to_expr(u, ind2var)

    def _to_expr(self, u, ind2var):
        if abs(u) == 1:
            return u
        i, v, w = self._succ[abs(u)]
        var = ind2var[i]
        p = self._to_expr(v, ind2var)
        q = self._to_expr(w, ind2var)
        # pure var ?
        if p == -1 and q == 1:
            return var
        elif p == 1 and q == -1:
            return '!{var}'.format(var=var)
        else:
            return '({var} -> {q} : {p})'.format(var=var, p=p, q=q)

    def apply(self, op, u, v=None):
        """Apply Boolean connective `op` between nodes `u` and `v`.

        @type op: `str` in:
          - `'not', 'or', 'and', 'xor', 'implies', 'bimplies'`
          - `'!', '|', '||', '&', '&&', '^', '->', '<->'`
        @type u, v: nodes
        """
        if op in {'not', '!'}:
            return -u
        elif op in {'or', '|', '||'}:
            return self.ite(u, 1, v)
        elif op in {'and', '&', '&&'}:
            return self.ite(u, v, -1)
        elif op in {'xor', '^'}:
            return self.ite(u, -v, v)
        elif op in {'implies', '->'}:
            return self.ite(u, v, 1)
        elif op in {'bimplies', '<->'}:
            return self.ite(u, v, -v)

    def dump_pdf(self, filename):
        """Write the BDD graph to `filename` as PDF."""
        g = to_pydot(self)
        if filename.endswith('.pdf'):
            g.write_pdf(filename)
        else:
            raise Exception('file type not supported')


def rename(u, bdd, dvars):
    """Efficient rename to non-essential neighbors.

    @param dvars: `dict` from variabe indices to variable indices
        or from variable names to variable names
    """
    assert u in bdd, u
    # map name to indices, if needed
    ordering = bdd.ordering
    k = next(iter(dvars))
    if k in ordering:
        dvars = {ordering[k]: ordering[v]
                 for k, v in dvars.iteritems()}
    # split
    var = set(dvars)
    varp = set(dvars.itervalues())  # primed vars
    # pairwise disjoint ?
    assert len(var) == len(varp), dvars
    assert not var.intersection(varp), dvars
    S = set()
    Q = set([u])
    # u independent of varp ?
    while Q:
        x = Q.pop()
        i, v, w = bdd._succ[abs(x)]
        if v is None or w is None:
            assert v is None, v
            assert w is None, w
            continue
        if v not in S:
            Q.add(v)
            S.add(v)
        if w not in S:
            Q.add(w)
            S.add(w)
        assert i not in varp, 'target var "{i}" is essential'.format(i=i)
    # neighbors ?
    for v, vp in dvars.iteritems():
        assert abs(v - vp) == 1, '"{v}" not neighbor of "{vp}"'.format(
            v=v, vp=vp)
    return _rename(u, bdd, dvars)


def _rename(u, bdd, dvars):
    """Recursive renaming, assuming `dvars` is valid."""
    if abs(u) == 1:
        return u
    i, v, w = bdd._succ[abs(u)]
    p = _rename(v, bdd, dvars)
    q = _rename(w, bdd, dvars)
    # to be renamed ?
    z = dvars.get(i, i)
    return bdd.find_or_add(z, p, q)


def image(trans, source, rename, qvars, bdd, forall=False):
    """Return set reachable from `source` under `trans`.

    @param trans: transition relation
    @param source: the transition must start in this set
    @param rename: `dict` that maps primed variables in
        `u` to unprimed variables in `u`.
        Applied to the quantified conjunction of `u` and `v`.
    @param qvars: `set` of quantified variables
    @param bdd: [BDD]
    @param forall: if `True`,
        then quantify `qvars` universally,
        else existentially.
    """
    cache = dict()
    rename_u = rename
    rename_v = None
    return _image(trans, source, rename_u, rename_v,
                  qvars, bdd, forall, cache)


def preimage(trans, target, rename, qvars, bdd, forall=False):
    """Return set that can reach target `v` under `u`.

    Also known as the "relational product".
    Assumes that primed and unprimed variables are neighbors.
    Variables are identified by their indices.

    @param trans: transition relation
    @param target: the transition must end in this set
    @param rename: `dict` that maps variables in `v` to
        variables in `u`
    @param qvars: `set` of quantified variables
    """
    cache = dict()
    rename_u = None
    rename_v = rename
    return _image(trans, target, rename_u, rename_v,
                  qvars, bdd, forall, cache)


def _image(u, v, umap, vmap, qvars, bdd, forall, cache):
    """Recursive (pre)image computation.

    @param u, v: nodes
    @param umap: renaming of variables in `u`
        that occurs after conjunction of `u` with `v`
        and quantification.
    @param vmap: renaming of variables in `v`
        that occurs before conjunction with `u`.
    """
    # controlling values for conjunction ?
    if u == -1 or v == -1:
        return -1
    if u == 1 and v == 1:
        return 1
    # already computed ?
    t = (u, v)
    w = cache.get(t)
    if w is not None:
        return w
    # recurse (descend)
    iu = bdd._succ[abs(u)][0]
    jv = bdd._succ[abs(v)][0]
    if vmap is None:
        iv = jv
    else:
        iv = vmap.get(jv, jv)
    z = min(iu, iv)
    u0, u1 = bdd._top_cofactor(u, z)
    v0, v1 = bdd._top_cofactor(v, jv + z - iv)
    p = _image(u0, v0, umap, vmap, qvars, bdd, forall, cache)
    q = _image(u1, v1, umap, vmap, qvars, bdd, forall, cache)
    # quantified ?
    if z in qvars:
        if forall:
            r = bdd.ite(p, q, -1)  # conjoin
        else:
            r = bdd.ite(p, 1, q)  # disjoin
    else:
        if umap is not None:
            m = umap.get(z, z)
        else:
            m = z
        r = bdd.find_or_add(m, p, q)
    cache[t] = r
    return r


def to_nx(bdd, u=None):
    """Convert `BDD` to `networkx.MultiDiGraph`.

    If a node `u` is given, then only the subgraph
    rooted at `u` is used.
    """
    import networkx as nx
    g = nx.MultiDiGraph()
    if u is None:
        assert bdd.roots, 'BDD has no roots: give a node `u`'
        roots = bdd.roots
    else:
        roots = {u}
    for root in roots:
        assert abs(root) in bdd, root
        Q = {root}
        while Q:
            u = Q.pop()
            i, v, w = bdd._succ[abs(u)]
            g.add_node(abs(u), index=i)
            # terminal ?
            if v is None or w is None:
                assert w is None, w
                assert v is None, v
                continue
            # non-terminal
            if v not in g:
                Q.add(v)
            if w not in g:
                Q.add(w)
            g.add_edge(u, abs(v), value=False, complement=(v < 0))
            g.add_edge(u, abs(w), value=True, complement=False)
    return g


def to_pydot(bdd):
    """Convert `BDD` to pydot graph.

    Nodes are ordered by variables.
    Edges to low successors are dashed.
    """
    import pydot
    g = pydot.Dot('bdd', graph_type='digraph')
    skeleton = list()
    subgraphs = dict()
    for i in xrange(len(bdd.ordering) + 1):
        h = pydot.Subgraph('', rank='same')
        g.add_subgraph(h)
        subgraphs[i] = h
        # add phantom node
        u = '-{i}'.format(i=i)
        skeleton.append(u)
        nd = pydot.Node(name=u, label=str(i), shape='none')
        h.add_node(nd)
    # auxiliary edges for ranking
    a, b = tee(skeleton)
    next(b, None)
    for u, v in izip(a, b):
        g.add_edge(pydot.Edge(str(u), str(v), style='invis'))
    # add nodes
    idx2var = {k: v for v, k in bdd.ordering.iteritems()}
    f = lambda x: str(abs(x))
    for u, (i, v, w) in bdd._succ.iteritems():
        # terminal ?
        if v is None:
            var = str(bool(abs(u)))
        else:
            var = idx2var[i]
        su = f(u)
        label = '{var}-{u}'.format(var=var, u=su)
        nd = pydot.Node(name=su, label=label)
        # add node to subgraph for level i
        h = subgraphs[i]
        h.add_node(nd)
        # add edges
        if v is None:
            continue
        sv = f(v)
        sw = f(w)
        vlabel = '-1' if v < 0 else ' '
        e = pydot.Edge(su, sv, style='dashed', label=vlabel)
        g.add_edge(e)
        e = pydot.Edge(su, sw, style='solid')
        g.add_edge(e)
    return g
